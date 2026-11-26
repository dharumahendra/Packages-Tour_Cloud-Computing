# Panduan lab — versi ECS (pengganti jalur Lambda)

## Kenapa berubah

Akun lab ini (voclabs) **tidak punya `LabRole`**, **tidak boleh `iam:CreateRole`**, dan **CloudShell
diblokir**. Satu-satunya role yang bisa dipakai Lambda (`…ECSEnforcerLambdaIamRole…`) hanya punya izin
`ecs:*` + logs, tanpa DynamoDB. Karena itu arsitektur disesuaikan ke role yang tersedia:

| Sebelumnya (Lambda) | Sekarang (sesuai role lab) | Role |
| --- | --- | --- |
| Lambda (2 fungsi) | **ECS Fargate** (2 service container) | `PipelineRole` (task + execution role) |
| API Gateway HTTP API | **Application Load Balancer** (`/admin/*` → operator) | — |
| DynamoDB | **S3** (1 objek JSON per paket) | `PipelineRole` punya `AmazonS3FullAccess` |
| CodeDeploy canary Lambda | **CodeDeploy blue/green ECS, canary 10% 5 menit** | `DeployRole` (`AWSCodeDeployRoleForECS`) |
| SAM | **Skrip boto3** `scripts/provision.py` (CloudFormation juga ditolak di akun lab) | — |
| CloudShell | **GitHub Actions** (build Docker + deploy); demo monolit di laptop | — |

Ini sama dengan arsitektur lab asli AWS Academy (ECS + ALB + CodeDeploy blue/green). Catat di bagian
**Kendala** laporan.

## Langkah

### 1. Hemat budget (console)
EC2 → Instances → `MonolithicAppServer` → **Instance state → Stop**. RDS → Databases → **Actions → Stop
temporarily**. (Arsitektur baru tidak memakai keduanya; VPC-nya tetap dipakai.)

### 2. Gambar 3 — monolit (di laptop, tanpa AWS)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\monolith-demo.ps1
```
Screenshot output tiga bagian (data bocor, kuota 999 diterima, hapus → 204) sebagai `g3-monolit.png`.

### 3. Gambar 5 bagian 1 — unit test
```powershell
python -m pytest -v tests     # 7 passed
```

### 4. GitHub Secrets (Settings → Secrets and variables → Actions → Secrets)
| Nama | Isi |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | dari Canvas → AWS Details → AWS CLI: Show |
| `AWS_SECRET_ACCESS_KEY` | idem |
| `AWS_SESSION_TOKEN` | idem (panjang, salin semua) |
| `ADMIN_KEY` | mis. `wisata2026rahasia` (huruf + angka) |

Tidak perlu `LAB_ROLE_ARN`/`DEPLOY_ROLE_ARN`: skrip otomatis memakai `PipelineRole` dan `DeployRole`.
Ketiga secret `AWS_*` **kedaluwarsa tiap sesi lab** — perbarui setiap Start Lab.

### 5. Push & deploy pertama (Gambar 5 bagian 2, Gambar 6)
```powershell
git push origin main
```
GitHub → Actions → `test-and-deploy`: job `test` lalu `deploy` (±8–12 menit pertama kali). Baris terakhir log
`Build and deploy` = **ApiUrl** (`http://tour-alb-….elb.amazonaws.com`).

Screenshot untuk Gambar 6:
- ECS → Clusters → `tour-cluster` → Services `tour-catalog`, `tour-operator` (Running 1/1).
- EC2 → Load Balancers → `tour-alb` → Listeners: port 80 dengan rule `/admin/*`, port 8080.
- CodeDeploy → Applications → `tour-app` (dua deployment group). S3 → bucket `tour-packages-<account>`. ECR → repo `tour-catalog`, `tour-operator`.

### 6. Tes end-to-end (tabel bagian 5)
Git Bash di laptop:
```bash
./scripts/e2e.sh http://<ApiUrl> wisata2026rahasia | tee docs/evidence/e2e.txt
```
Hasil yang diharapkan sama dengan tabel di panduan lama (403, 201, 200 tanpa costPrice, 200, 400, 200 lalu 404, 204).

### 7. Ubah kode → canary (Gambar 7)
Edit `services/catalog/app.py`: `VERSION = "1.1"` + fungsi `availability` (sama seperti panduan lama), lalu
```bash
python -m pytest -q tests
git commit -am "feat(catalog): add availability label, bump to 1.1" && git push
```
Saat job `deploy` menulis *Menunggu CodeDeploy*: CodeDeploy → Deployments → deployment **catalog** (hanya
catalog, karena kode operator tidak berubah) → screenshot *Traffic shifting* 10% / 90% = `g7-canary.png`.
Selama canary (Git Bash):
```bash
for i in $(seq 1 40); do curl -s $API/health; echo; done | sort | uniq -c
```
→ campuran `"version": "1.0"` (±90%) dan `"1.1"` (±10%).

**Tes sengaja gagal:** ubah sementara `== 200` jadi `== 500` di `test_health`, commit + push → job `test`
merah, `deploy` skipped. Lalu `git revert HEAD --no-edit && git push`.

### 8. Batasi akses operator (bagian 5)
GitHub → Variables → `ALLOWED_IPS` = IP lain (mis. `1.1.1.1`) → Run workflow → dari laptop
`curl -s -o /dev/null -w "%{http_code}\n" $API/admin/packages -H "x-admin-key: <key>"` → **403**.
Ganti `ALLOWED_IPS` dengan IP laptop (`curl https://checkip.amazonaws.com`) → Run workflow → **200**.
Katalog publik `curl $API/packages` tetap 200. Setelah selesai hapus variable dan jalankan ulang workflow.
(Perubahan env juga dirilis lewat CodeDeploy, jadi tiap langkah ±6 menit.)

### 9. Uji beban + CloudWatch (Gambar 8)
```bash
seq 1 300 | xargs -P 25 -I{} curl -s -o /dev/null -w "%{http_code}\n" $API/packages | sort | uniq -c
```
Ulangi 2–3 kali. CloudWatch → Metrics → **ApplicationELB → Per AppELB, per TG Metrics** → `RequestCount`
untuk kedua target group, dan **ECS → ClusterName, ServiceName** → `CPUUtilization`; periode 1 menit,
rentang 1 jam → `g8-cloudwatch.png` (catalog naik, operator datar).

### 10. Cleanup (setelah dinilai)
Git Bash dengan kredensial lab: `python scripts/provision.py destroy`, lalu End Lab.

## Troubleshooting
| Gejala | Solusi |
| --- | --- |
| `ExpiredToken` / `InvalidClientTokenId` di Actions | Perbarui 3 secret `AWS_*` |
| `AWS menolak <Operasi>: AccessDenied` | Izin lab tidak mencakup operasi itu; kirim pesannya untuk disesuaikan |
| `not authorized … ecs:CreateCluster` / `elasticloadbalancing:*` / `iam:CreateServiceLinkedRole` | Izin lab tidak cukup untuk ECS/ALB → laporkan ke dosen/asisten dengan pesan errornya |
| Service tidak sehat (target unhealthy) | ECS → service → Logs; cek task bisa pull image (subnet publik + public IP) |
| CodeDeploy gagal | CodeDeploy → deployment → lihat event; rollback otomatis ke versi lama |
| `403 Akses operator ditolak` padahal key benar | Cek variable `ALLOWED_IPS` |
