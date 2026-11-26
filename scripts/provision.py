"""Provision dan rilis paket wisata di AWS langsung lewat API (boto3).

Akun lab (voclabs) tidak boleh memakai CloudFormation, jadi semua resource dibuat di sini secara
idempoten: dijalankan berulang kali aman, resource yang sudah ada dipakai ulang.

  python3 scripts/provision.py base                -> repo ECR + bucket S3 (mencetak nama bucket)
  python3 scripts/provision.py app --catalog-image URI --operator-image URI --bucket NAMA
                                                   -> ALB, ECS, CodeDeploy; rilis canary bila sudah ada
  python3 scripts/provision.py destroy             -> hapus semua resource (cleanup akhir lab)

Variabel lingkungan: STACK_PREFIX (default tour), ADMIN_KEY, ALLOWED_IPS, TASK_ROLE_ARN,
DEPLOY_ROLE_ARN, DEPLOYMENT_CONFIG, VPC_ID, SUBNET_IDS.
"""
import argparse
import json
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

PREFIX = os.environ.get("STACK_PREFIX") or "tour"
SERVICES = {
    # nama: (path health check, butuh rahasia operator?)
    "catalog": ("/health", False),
    "operator": ("/admin/health", True),
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def code(err):
    return err.response["Error"]["Code"]


def account_id():
    return boto3.client("sts").get_caller_identity()["Account"]


def region():
    return boto3.session.Session().region_name or "us-east-1"


# ---------------------------------------------------------------- base
def base(_args):
    ecr = boto3.client("ecr")
    for svc in SERVICES:
        try:
            ecr.create_repository(repositoryName=f"{PREFIX}-{svc}")
            log(f"ECR repo {PREFIX}-{svc} dibuat")
        except ClientError as e:
            if code(e) != "RepositoryAlreadyExistsException":
                raise

    s3 = boto3.client("s3")
    bucket = f"{PREFIX}-packages-{account_id()}"
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)
        log(f"Bucket {bucket} dibuat (bucket baru S3 otomatis memblokir akses publik)")
    print(bucket)


# ---------------------------------------------------------------- jaringan
def network(ec2):
    vpc = os.environ.get("VPC_ID")
    if not vpc:
        res = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": ["MonolithicAppServer"]}])
        for r in res["Reservations"]:
            vpc = r["Instances"][0]["VpcId"]
    if not vpc:
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        vpc = vpcs[0]["VpcId"] if vpcs else None
    if not vpc:
        sys.exit("VPC tidak ditemukan. Isi VPC_ID dan SUBNET_IDS.")

    if os.environ.get("SUBNET_IDS"):
        subnets = os.environ["SUBNET_IDS"].split(",")
    else:
        found = ec2.describe_subnets(Filters=[
            {"Name": "vpc-id", "Values": [vpc]},
            {"Name": "map-public-ip-on-launch", "Values": ["true"]}])["Subnets"]
        per_az = {}
        for s in sorted(found, key=lambda s: s["SubnetId"]):
            per_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
        subnets = [per_az[az] for az in sorted(per_az)][:2]
    if len(subnets) < 2:
        sys.exit(f"Butuh 2 subnet publik di AZ berbeda di {vpc}; ketemu {subnets}. Isi SUBNET_IDS.")
    log(f"VPC {vpc}, subnet {','.join(subnets)}")
    return vpc, subnets


def security_group(ec2, vpc, name, description, rules):
    found = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc]}, {"Name": "group-name", "Values": [name]}])["SecurityGroups"]
    if found:
        return found[0]["GroupId"]
    gid = ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=rules)
    log(f"Security group {name} dibuat")
    return gid


def target_group(elb, name, vpc, path):
    try:
        return elb.describe_target_groups(Names=[name])["TargetGroups"][0]["TargetGroupArn"]
    except ClientError as e:
        if code(e) != "TargetGroupNotFound":
            raise
    arn = elb.create_target_group(
        Name=name, Protocol="HTTP", Port=8080, VpcId=vpc, TargetType="ip",
        HealthCheckPath=path, HealthCheckIntervalSeconds=10, HealthyThresholdCount=2,
    )["TargetGroups"][0]["TargetGroupArn"]
    elb.modify_target_group_attributes(TargetGroupArn=arn, Attributes=[
        {"Key": "deregistration_delay.timeout_seconds", "Value": "10"}])
    log(f"Target group {name} dibuat")
    return arn


def forward(tg_arn):
    return [{"Type": "forward", "TargetGroupArn": tg_arn}]


def has_admin_rule(rules):
    for rule in rules:
        for cond in rule["Conditions"]:
            values = cond.get("Values") or cond.get("PathPatternConfig", {}).get("Values") or []
            if "/admin/*" in values:
                return True
    return False


def infrastructure():
    ec2, elb = boto3.client("ec2"), boto3.client("elbv2")
    ecs, logs, cd = boto3.client("ecs"), boto3.client("logs"), boto3.client("codedeploy")
    vpc, subnets = network(ec2)

    alb_sg = security_group(ec2, vpc, f"{PREFIX}-alb-sg", "ALB paket wisata (80 prod, 8080 tes)", [
        {"IpProtocol": "tcp", "FromPort": p, "ToPort": p, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
        for p in (80, 8080)])
    task_sg = security_group(ec2, vpc, f"{PREFIX}-task-sg", "Task ECS paket wisata, hanya dari ALB", [
        {"IpProtocol": "tcp", "FromPort": 8080, "ToPort": 8080, "UserIdGroupPairs": [{"GroupId": alb_sg}]}])

    try:
        lb = elb.describe_load_balancers(Names=[f"{PREFIX}-alb"])["LoadBalancers"][0]
    except ClientError as e:
        if code(e) != "LoadBalancerNotFound":
            raise
        lb = elb.create_load_balancer(Name=f"{PREFIX}-alb", Subnets=subnets, SecurityGroups=[alb_sg],
                                      Scheme="internet-facing", Type="application")["LoadBalancers"][0]
        log(f"ALB {PREFIX}-alb dibuat")

    tgs = {}
    for svc, (path, _) in SERVICES.items():
        for color in ("blue", "green"):
            tgs[svc, color] = target_group(elb, f"{PREFIX}-{svc}-{color}", vpc, path)

    listeners = {l["Port"]: l["ListenerArn"]
                 for l in elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"]}
    for port, color in ((80, "blue"), (8080, "green")):
        if port not in listeners:
            listeners[port] = elb.create_listener(
                LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=port,
                DefaultActions=forward(tgs["catalog", color]))["Listeners"][0]["ListenerArn"]
            log(f"Listener :{port} dibuat")
        if not has_admin_rule(elb.describe_rules(ListenerArn=listeners[port])["Rules"]):
            elb.create_rule(ListenerArn=listeners[port], Priority=10,
                            Conditions=[{"Field": "path-pattern", "Values": ["/admin/*"]}],
                            Actions=forward(tgs["operator", color]))

    cluster = f"{PREFIX}-cluster"
    found = ecs.describe_clusters(clusters=[cluster])["clusters"]
    if not found or found[0]["status"] != "ACTIVE":
        ecs.create_cluster(clusterName=cluster)
        log(f"Cluster {cluster} dibuat")

    log_group = f"/ecs/{PREFIX}"
    try:
        logs.create_log_group(logGroupName=log_group)
        logs.put_retention_policy(logGroupName=log_group, retentionInDays=7)
    except ClientError as e:
        # Akun lab tidak boleh CreateLogGroup; container membuatnya sendiri (awslogs-create-group)
        log(f"Log group tidak dibuat dari sini ({code(e)}); dibuat oleh task ECS")

    app = f"{PREFIX}-app"
    try:
        cd.create_application(applicationName=app, computePlatform="ECS")
        log(f"CodeDeploy application {app} dibuat")
    except ClientError as e:
        if code(e) != "ApplicationAlreadyExistsException":
            raise

    return {"subnets": subnets, "task_sg": task_sg, "lb": lb, "tgs": tgs, "listeners": listeners,
            "cluster": cluster, "log_group": log_group, "app": app}


# ---------------------------------------------------------------- ECS + CodeDeploy
def container_def(svc, image, env, log_group):
    return {
        "name": svc, "image": image, "essential": True,
        "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
        "environment": [{"name": k, "value": v} for k, v in sorted(env.items())],
        "logConfiguration": {"logDriver": "awslogs", "options": {
            "awslogs-group": log_group, "awslogs-region": region(), "awslogs-stream-prefix": svc,
            "awslogs-create-group": "true"}},
    }


def register(ecs, svc, image, env, infra, role):
    return ecs.register_task_definition(
        family=f"{PREFIX}-{svc}", cpu="256", memory="512", networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"], executionRoleArn=role, taskRoleArn=role,
        containerDefinitions=[container_def(svc, image, env, infra["log_group"])],
    )["taskDefinition"]["taskDefinitionArn"]


def ensure_deployment_group(cd, svc, infra, deploy_role):
    group = f"{PREFIX}-{svc}"
    try:
        cd.get_deployment_group(applicationName=infra["app"], deploymentGroupName=group)
        return group
    except ClientError as e:
        if code(e) != "DeploymentGroupDoesNotExistException":
            raise
    cd.create_deployment_group(
        applicationName=infra["app"], deploymentGroupName=group, serviceRoleArn=deploy_role,
        deploymentConfigName=os.environ.get("DEPLOYMENT_CONFIG") or "CodeDeployDefault.ECSCanary10Percent5Minutes",
        deploymentStyle={"deploymentType": "BLUE_GREEN", "deploymentOption": "WITH_TRAFFIC_CONTROL"},
        blueGreenDeploymentConfiguration={
            "deploymentReadyOption": {"actionOnTimeout": "CONTINUE_DEPLOYMENT", "waitTimeInMinutes": 0},
            "terminateBlueInstancesOnDeploymentSuccess": {"action": "TERMINATE", "terminationWaitTimeInMinutes": 0},
        },
        autoRollbackConfiguration={"enabled": True, "events": ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_REQUEST"]},
        ecsServices=[{"serviceName": f"{PREFIX}-{svc}", "clusterName": infra["cluster"]}],
        loadBalancerInfo={"targetGroupPairInfoList": [{
            "targetGroups": [{"name": f"{PREFIX}-{svc}-blue"}, {"name": f"{PREFIX}-{svc}-green"}],
            "prodTrafficRoute": {"listenerArns": [infra["listeners"][80]]},
            "testTrafficRoute": {"listenerArns": [infra["listeners"][8080]]},
        }]},
    )
    log(f"Deployment group {group} dibuat")
    return group


def release(ecs, cd, svc, service, image, env, infra, group):
    """Rilis canary lewat CodeDeploy bila image/env berubah. Mengembalikan ID deployment atau None."""
    current = ecs.describe_task_definition(taskDefinition=service["taskDefinition"])["taskDefinition"]
    container = next(c for c in current["containerDefinitions"] if c["name"] == svc)
    old_env = {e["name"]: e["value"] for e in container.get("environment", [])}
    if container["image"] == image and old_env == env:
        log(f"[{svc}] tidak ada perubahan, rilis dilewati")
        return None
    td = register(ecs, svc, image, env, infra, current["taskRoleArn"])
    appspec = {"version": 0.0, "Resources": [{"TargetService": {"Type": "AWS::ECS::Service", "Properties": {
        "TaskDefinition": td, "LoadBalancerInfo": {"ContainerName": svc, "ContainerPort": 8080}}}}]}
    dep = cd.create_deployment(
        applicationName=infra["app"], deploymentGroupName=group, description=f"{svc} -> {image}",
        revision={"revisionType": "AppSpecContent", "appSpecContent": {"content": json.dumps(appspec)}},
    )["deploymentId"]
    log(f"[{svc}] CodeDeploy deployment {dep} dimulai (canary)")
    return dep


def wait_deployments(cd, ids, timeout=1800):
    pending, failed, last = set(ids), [], {}
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for dep in sorted(pending):
            info = cd.get_deployment(deploymentId=dep)["deploymentInfo"]
            status = info["status"]
            if last.get(dep) != status:
                log(f"  {dep} ({info.get('deploymentGroupName')}): {status}")
                last[dep] = status
            if status in ("Succeeded", "Failed", "Stopped"):
                pending.discard(dep)
                if status != "Succeeded":
                    failed.append(f"{dep}: {info.get('errorInformation', {}).get('message', status)}")
        if pending:
            time.sleep(15)
    failed += [f"{d}: timeout" for d in pending]
    if failed:
        sys.exit("Deployment gagal:\n  " + "\n  ".join(failed))


def app(args):
    ecs, cd = boto3.client("ecs"), boto3.client("codedeploy")
    account = account_id()
    task_role = os.environ.get("TASK_ROLE_ARN") or f"arn:aws:iam::{account}:role/PipelineRole"
    deploy_role = os.environ.get("DEPLOY_ROLE_ARN") or f"arn:aws:iam::{account}:role/DeployRole"
    infra = infrastructure()

    images = {"catalog": args.catalog_image, "operator": args.operator_image}
    created, deployments = [], []
    for svc, (_, secret) in SERVICES.items():
        env = {"PACKAGES_BUCKET": args.bucket}
        if secret:
            env.update({"ADMIN_KEY": os.environ.get("ADMIN_KEY", ""), "ALLOWED_IPS": os.environ.get("ALLOWED_IPS", "")})
        name = f"{PREFIX}-{svc}"
        found = ecs.describe_services(cluster=infra["cluster"], services=[name])["services"]
        if found and found[0]["status"] == "ACTIVE":
            group = ensure_deployment_group(cd, svc, infra, deploy_role)
            dep = release(ecs, cd, svc, found[0], images[svc], env, infra, group)
            if dep:
                deployments.append(dep)
            continue
        td = register(ecs, svc, images[svc], env, infra, task_role)
        ecs.create_service(
            cluster=infra["cluster"], serviceName=name, taskDefinition=td, desiredCount=1,
            launchType="FARGATE", healthCheckGracePeriodSeconds=30,
            deploymentController={"type": "CODE_DEPLOY"},
            loadBalancers=[{"targetGroupArn": infra["tgs"][svc, "blue"], "containerName": svc, "containerPort": 8080}],
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": infra["subnets"], "securityGroups": [infra["task_sg"]], "assignPublicIp": "ENABLED"}},
        )
        log(f"Service {name} dibuat")
        ensure_deployment_group(cd, svc, infra, deploy_role)
        created.append(name)

    if created:
        log("Menunggu service pertama kali sehat (bisa 3-5 menit)...")
        ecs.get_waiter("services_stable").wait(cluster=infra["cluster"], services=created,
                                               WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    if deployments:
        log("Menunggu CodeDeploy (canary 10% selama 5 menit, lalu 100%)...")
        wait_deployments(cd, deployments)
    print(f"http://{infra['lb']['DNSName']}")


# ---------------------------------------------------------------- cleanup
def destroy(_args):
    ec2, elb, ecs = boto3.client("ec2"), boto3.client("elbv2"), boto3.client("ecs")
    cd, ecr, s3, logs = boto3.client("codedeploy"), boto3.client("ecr"), boto3.resource("s3"), boto3.client("logs")

    def attempt(label, fn):
        try:
            fn()
            log(f"dihapus: {label}")
        except ClientError as e:
            log(f"lewati {label}: {code(e)}")

    cluster = f"{PREFIX}-cluster"
    for svc in SERVICES:
        attempt(f"service {svc}", lambda s=svc: ecs.delete_service(cluster=cluster, service=f"{PREFIX}-{s}", force=True))
    attempt("CodeDeploy app", lambda: cd.delete_application(applicationName=f"{PREFIX}-app"))
    try:
        lb = elb.describe_load_balancers(Names=[f"{PREFIX}-alb"])["LoadBalancers"][0]
        elb.delete_load_balancer(LoadBalancerArn=lb["LoadBalancerArn"])
        elb.get_waiter("load_balancers_deleted").wait(LoadBalancerArns=[lb["LoadBalancerArn"]])
        log("dihapus: ALB")
    except ClientError as e:
        log(f"lewati ALB: {code(e)}")
    time.sleep(20)
    for svc in SERVICES:
        for color in ("blue", "green"):
            def drop(name=f"{PREFIX}-{svc}-{color}"):
                arn = elb.describe_target_groups(Names=[name])["TargetGroups"][0]["TargetGroupArn"]
                elb.delete_target_group(TargetGroupArn=arn)
            attempt(f"target group {svc}-{color}", drop)
    attempt("cluster", lambda: ecs.delete_cluster(cluster=cluster))
    for name in (f"{PREFIX}-task-sg", f"{PREFIX}-alb-sg"):
        def drop_sg(name=name):
            for sg in ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [name]}])["SecurityGroups"]:
                for _ in range(20):  # ENI task/ALB butuh waktu untuk lepas
                    try:
                        ec2.delete_security_group(GroupId=sg["GroupId"])
                        break
                    except ClientError as e:
                        if code(e) != "DependencyViolation":
                            raise
                        time.sleep(15)
        attempt(f"security group {name}", drop_sg)
    for svc in SERVICES:
        attempt(f"ECR {svc}", lambda s=svc: ecr.delete_repository(repositoryName=f"{PREFIX}-{s}", force=True))
    def drop_bucket():
        bucket = s3.Bucket(f"{PREFIX}-packages-{account_id()}")
        bucket.objects.all().delete()
        bucket.delete()
    attempt("bucket S3", drop_bucket)
    attempt("log group", lambda: logs.delete_log_group(logGroupName=f"/ecs/{PREFIX}"))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("base")
    a = sub.add_parser("app")
    a.add_argument("--catalog-image", required=True)
    a.add_argument("--operator-image", required=True)
    a.add_argument("--bucket", required=True)
    sub.add_parser("destroy")
    args = parser.parse_args()
    try:
        {"base": base, "app": app, "destroy": destroy}[args.cmd](args)
    except ClientError as e:
        op = e.operation_name
        sys.exit(f"AWS menolak {op}: {code(e)} - {e.response['Error'].get('Message', '')}")


if __name__ == "__main__":
    main()
