# Panduan screenshot lengkap (G1–G8)

**ApiUrl:** `http://tour-alb-1447830951.us-east-1.elb.amazonaws.com`
**Folder project:** `D:\Mata_Kuliah\SEM 5\Packages Tour_Cloud Computing`
Simpan semua screenshot di `docs\evidence\` dengan nama di judul tiap bagian.

> Setiap buka terminal baru, **pindah dulu ke folder project**:
> - PowerShell: `cd "D:\Mata_Kuliah\SEM 5\Packages Tour_Cloud Computing"`
> - Git Bash:  `cd "/d/Mata_Kuliah/SEM 5/Packages Tour_Cloud Computing"`
>
> Python hanya jalan di **PowerShell**; perintah `curl`/`xargs`/`seq` dijalankan di **Git Bash**.
> Kalau lab di-Start ulang: perbarui 3 secret `AWS_*` di GitHub dulu (AWS Details → AWS CLI: Show).

---

## 0. Hemat budget (sekali saja)
Console AWS → **EC2 → Instances** → centang `MonolithicAppServer` → **Instance state → Stop instance**.
**RDS → Databases** → pilih database → **Actions → Stop temporarily**. (Arsitektur baru tidak memakainya.)

## G1 — Arsitektur (`g1-arsitektur.png`)
1. Buka https://app.diagrams.net → **Open Existing Diagram** → **Device** → pilih `docs\architecture.drawio`.
2. Rapikan posisi kalau perlu (seret ikon).
3. **File → Export as → PNG** → Border 10 → Export → simpan `docs\evidence\g1-arsitektur.png`.

## G2 — Estimasi biaya (`g2-biaya.png`)
https://calculator.aws → **Create estimate**, region **US East (N. Virginia)**, tambah layanan:

| Layanan | Isian | ±USD/bulan |
| --- | --- | --- |
| AWS Fargate | Linux x86, 2 task, 24 jam/hari (730 jam), 0.25 vCPU, 0.5 GB memory, 20 GB storage | ±18 |
| Elastic Load Balancing | Application LB, 1 ALB, processed bytes 1 GB/bulan, 10 koneksi baru/detik | ±17 |
| Amazon S3 | S3 Standard 1 GB, 10.000 PUT, 100.000 GET | ±0,1 |
| Amazon ECR | storage 1 GB | ±0,1 |
| Amazon CloudWatch | Logs ingested 1 GB | ±0,5 |
| Amazon VPC → Public IPv4 address | 4 alamat in-use (2 task + 2 node ALB), 730 jam | ±14,6 |

Total ±$50/bulan (±$603/12 bulan). Screenshot halaman **My estimate** yang menampilkan semua layanan + total.
Di laporan: biaya dominan dari ALB dan Fargate yang menyala 24 jam (berbeda dari Lambda yang bayar per request).

## G3 — Masalah monolit (`g3-monolit.png`)
PowerShell:
```powershell
cd "D:\Mata_Kuliah\SEM 5\Packages Tour_Cloud Computing"
powershell -ExecutionPolicy Bypass -File scripts\monolith-demo.ps1
```
Screenshot output 3 bagian: data internal bocor, kuota 999 diterima, hapus → 204.

## G4 — Pipeline CI/CD (`g4-cicd.png`)
Sama seperti G1, buka `docs\cicd.drawio` → Export PNG → `docs\evidence\g4-cicd.png`.

## G5 — Unit test + GitHub Actions (`g5-pytest-actions.png`)
1. PowerShell (di folder project): `python -m pytest -v tests` → screenshot **7 passed**.
2. GitHub → **Actions** → klik run hijau teratas → screenshot graf **test ✅ → deploy ✅**.
   Gabungkan dua gambar jadi satu (atau pakai keduanya).

## G6 — Resource di AWS (`g6-resources.png`)
Console AWS (region N. Virginia), screenshot masing-masing:
1. **ECS → Clusters → `tour-cluster`** → tab Services: `tour-catalog`, `tour-operator` status Active, Tasks 1/1 running.
2. **EC2 → Load Balancers → `tour-alb`** → tab **Listeners and rules**: port 80 (rule `/admin/*` → operator) dan port 8080.
3. **CodeDeploy → Applications → `tour-app`**: deployment group `tour-catalog` dan `tour-operator`.
4. **S3** → bucket `tour-packages-369196016238` (folder `packages/`).
5. **ECR → Repositories**: `tour-catalog`, `tour-operator`.

## Tes end-to-end (sudah selesai ✅)
Output tersimpan di `docs\evidence\e2e.txt`. Ulangi kalau perlu (Git Bash):
```bash
./scripts/e2e.sh http://tour-alb-1447830951.us-east-1.elb.amazonaws.com <ADMIN_KEY> | tee docs/evidence/e2e.txt
```

## G7 — Canary release (`g7-canary.png`)
Siapkan 2 tab dulu: **GitHub → Actions** dan **CodeDeploy → Deployments** (console AWS).

1. Git Bash (folder project) — ubah versi catalog lalu push:
   ```bash
   sed -i 's/VERSION = "1.0"/VERSION = "1.1"/' services/catalog/app.py
   git commit -am "feat(catalog): bump to 1.1" && git push
   ```
2. Tunggu ±2–3 menit sampai log job `deploy` menulis *Menunggu CodeDeploy*.
3. **CodeDeploy → Deployments** → klik deployment terbaru (grup `tour-catalog`) → tunggu bagian
   **Traffic shifting progress** menunjukkan **Original 90% / Replacement 10%** → **screenshot** (jendela 5 menit!).
4. Selama 5 menit itu, Git Bash — bukti trafik campuran:
   ```bash
   API=http://tour-alb-1447830951.us-east-1.elb.amazonaws.com
   for i in $(seq 1 40); do curl -s $API/health; echo; done | sort | uniq -c
   ```
   Harus muncul `"version": "1.0"` (±36x) dan `"version": "1.1"` (±4x) → screenshot.
5. Setelah ±6 menit deployment **Succeeded** dan semua respons `1.1`.

**Tes gagal (pipeline berhenti):**
```bash
sed -i 's/== 200/== 500/' tests/test_services.py
git commit -am "test: sengaja gagal" && git push
```
GitHub Actions: `test` ❌ merah, `deploy` **skipped** → screenshot. Lalu kembalikan:
```bash
git revert HEAD --no-edit && git push
```

## Batasi akses operator (allowlist IP)
1. Git Bash: `curl https://checkip.amazonaws.com` → catat IP laptop.
2. GitHub → **Settings → Secrets and variables → Actions → tab Variables → New repository variable**:
   Name `ALLOWED_IPS`, Value `1.1.1.1` (IP palsu).
3. **Actions → test-and-deploy → Run workflow** (main). Tunggu selesai (±8 menit, rilis canary).
4. Git Bash:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" $API/admin/packages -H "x-admin-key: <ADMIN_KEY>"   # 403
   curl -s -o /dev/null -w "%{http_code}\n" $API/packages                                       # 200
   ```
   Screenshot (403 untuk operator, katalog publik tetap 200).
5. Ubah variable `ALLOWED_IPS` = IP laptop → Run workflow lagi → ulangi curl operator → **200** → screenshot.
6. Selesai: hapus variable `ALLOWED_IPS` → Run workflow (akses kembali terbuka dengan key saja).

## G8 — Uji beban + CloudWatch (`g8-cloudwatch.png`)
1. Git Bash, ulangi 3 kali dengan jeda ±1 menit:
   ```bash
   seq 1 300 | xargs -P 25 -I{} curl -s -o /dev/null -w "%{http_code}\n" $API/packages | sort | uniq -c
   ```
   Harus `300 200`. Screenshot salah satu.
2. Tunggu 2–3 menit. **CloudWatch → Metrics → All metrics**:
   - **ApplicationELB → Per AppELB, per TG Metrics** → centang `RequestCount` untuk TG catalog dan operator.
   - Tab **Graphed metrics**: Statistic **Sum**, Period **1 minute**; rentang waktu **1h**.
   - Tambah juga **ECS → ClusterName, ServiceName** → `CPUUtilization` untuk kedua service (Average).
3. Screenshot grafik: catalog naik tajam, operator datar → bukti service bisa diskalakan terpisah.

## Cleanup (setelah dinilai)
GitHub → **Actions → cleanup → Run workflow**. Ini menghapus ECS, ALB, target group, CodeDeploy, ECR,
bucket S3, security group. Cek hasilnya di log, lalu **End Lab**.
