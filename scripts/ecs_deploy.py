"""Rilis versi baru satu service ECS lewat CodeDeploy (blue/green canary).

Dipanggil oleh scripts/deploy.sh setelah image di-push ke ECR.
- deploy: buat revisi task definition baru (image/env baru). Kalau tidak ada yang berubah, dilewati.
          Mencetak ID deployment CodeDeploy ke stdout.
- wait:   tunggu deployment selesai; exit 1 kalau gagal (CodeDeploy otomatis rollback).
"""
import argparse
import json
import sys
import time

import boto3

TASKDEF_KEYS = ["family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions",
                "volumes", "placementConstraints", "requiresCompatibilities", "cpu", "memory",
                "runtimePlatform", "ephemeralStorage"]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def deploy(args):
    ecs, codedeploy = boto3.client("ecs"), boto3.client("codedeploy")
    service = ecs.describe_services(cluster=args.cluster, services=[args.service])["services"][0]
    current = ecs.describe_task_definition(taskDefinition=service["taskDefinition"])["taskDefinition"]
    container = next(c for c in current["containerDefinitions"] if c["name"] == args.container)

    old_env = {e["name"]: e["value"] for e in container.get("environment", [])}
    new_env = dict(old_env)
    for pair in args.env or []:
        key, _, value = pair.partition("=")
        new_env[key] = value

    if container["image"] == args.image and new_env == old_env:
        log(f"[{args.service}] tidak ada perubahan (image & env sama), deploy dilewati")
        return

    container["image"] = args.image
    container["environment"] = [{"name": k, "value": v} for k, v in sorted(new_env.items())]
    new_def = ecs.register_task_definition(**{k: current[k] for k in TASKDEF_KEYS if k in current})
    taskdef_arn = new_def["taskDefinition"]["taskDefinitionArn"]
    log(f"[{args.service}] task definition baru: {taskdef_arn}")

    appspec = {
        "version": 0.0,
        "Resources": [{"TargetService": {"Type": "AWS::ECS::Service", "Properties": {
            "TaskDefinition": taskdef_arn,
            "LoadBalancerInfo": {"ContainerName": args.container, "ContainerPort": 8080},
        }}}],
    }
    deployment_id = codedeploy.create_deployment(
        applicationName=args.app,
        deploymentGroupName=args.group,
        description=f"{args.service} -> {args.image}",
        revision={"revisionType": "AppSpecContent", "appSpecContent": {"content": json.dumps(appspec)}},
    )["deploymentId"]
    log(f"[{args.service}] CodeDeploy deployment {deployment_id} dimulai")
    print(deployment_id)


def wait(args):
    codedeploy = boto3.client("codedeploy")
    pending, failed, last = set(args.ids), [], {}
    deadline = time.time() + args.timeout
    while pending and time.time() < deadline:
        for dep_id in sorted(pending):
            info = codedeploy.get_deployment(deploymentId=dep_id)["deploymentInfo"]
            status = info["status"]
            if last.get(dep_id) != status:
                log(f"{dep_id} ({info.get('deploymentGroupName')}): {status}")
                last[dep_id] = status
            if status in ("Succeeded", "Failed", "Stopped"):
                pending.discard(dep_id)
                if status != "Succeeded":
                    failed.append(f"{dep_id}: {info.get('errorInformation', {}).get('message', status)}")
        if pending:
            time.sleep(15)
    if pending:
        failed.extend(f"{d}: timeout" for d in pending)
    if failed:
        log("Deployment gagal:\n  " + "\n  ".join(failed))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy")
    for name in ("--cluster", "--service", "--container", "--image", "--app", "--group"):
        d.add_argument(name, required=True)
    d.add_argument("--env", action="append", help="KEY=VALUE, boleh diulang")
    w = sub.add_parser("wait")
    w.add_argument("ids", nargs="*")
    w.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    (deploy if args.cmd == "deploy" else wait)(args)


if __name__ == "__main__":
    main()
