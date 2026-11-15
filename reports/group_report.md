# Group Report — Day 06 Data Lab (Field-to-Release PoC)

> Điền đầy đủ, cụ thể, không viết chung chung. Mọi con số phải lấy từ file do script sinh ra
> (`reports/metrics_*.json`, `reports/table1_baseline_vs_poc.md`) — không gõ tay, không làm tròn mất đơn vị.
> 3–5 trang (hoặc README + report.md tương đương).

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | G01 |
| Challenge | T02 · Raw QC Triage |
| Thành viên (5–6) + vai trò | Trần Đức Hải (`haiitran`) — cá nhân: Data & fixtures, Core algorithm, QA/metric, Review + report|
| Git URL | https://github.com/HaiiTran/K5-Day6-L2-2A202603151-TranDucHai-DataGovernanceAndPrivacy |
| Tag `demo-180m` (commit) | Chưa tạo |
| Tag `final-24h` (commit) | Chưa tạo |
| Deadline (giờ VN) | - |

## 01 · Problem statement

- Ai bị ảnh hưởng / ai chịu hậu quả khi hệ thống sai? Người vận hành QC; ảnh lỗi lọt qua có thể làm giảm chất lượng dữ liệu dùng sau đó.
- Điểm nghẽn cụ thể: Ưu tiên 20 ảnh cần review trong pool cố định 96 ảnh.
- Đầu vào → đầu ra: `fixtures/t02_raw_qc/images.csv` và ảnh → score/ranked review top-20 → KEEP/RECAPTURE và decision.
- Tiêu chí GO / HOLD / REWORK (chốt trước khi chạy eval): HOLD nếu còn pending/escalate; REWORK nếu xác nhận ảnh severity 2 cần chụp lại; GO nếu review xong và không còn ảnh cần chụp lại.
- Giả định của nhóm (cần DPO/legal/domain owner xác nhận): score chỉ ưu tiên review, quyết định cuối do người review; fixture synthetic không đại diện đầy đủ ảnh thật.

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---|---|---|---|
| dev | 56 | `fixtures/t02_raw_qc/` | CC0-1.0 | Synthetic |
| eval / heldout | 40 | `fixtures/t02_raw_qc/` | CC0-1.0 | Synthetic |

- Schema & class mapping: `image_id, path, camera_id, light, split`; defect type `none, blur, glare, dark, compression`; severity 0–2.
- Seed: 42 · Cách chia split (theo scene/group/segment?): theo scene gốc.
- `manifest.csv` verified (sha256): Có ; lệnh: `python -m tools.verify_manifests`
- Dữ liệu thật dùng (nếu có) và cách xây GT cho nó: Không dùng; dev labels là tự review, không phải GT coach.

## 03 · Research & design

- 2–3 references (URL + ngày xem) và **vì sao** chọn kỹ thuật: Chưa bổ sung nghiên cứu ngoài.
- Sơ đồ data flow (input → process → rank/QC → review → decision → report): manifest/images → feature ảnh → score/rank top-20 → review/override/audit → GO/HOLD/REWORK → report.
- Thành phần offline (model/weights + checksum + license nếu có): Không dùng model/weights; feature ảnh chạy CPU, offline.
- Trade-offs đã cân nhắc: heuristic giải thích được và chạy offline; có thể nhầm ảnh tối tự nhiên hoặc glare cục bộ.

## 04 · Baseline vs PoC

- Baseline (mô tả, version/config, commit):
- PoC (mô tả, version/config, commit): `src/pipeline.py`; heuristic blur/brightness/glare, B=20, seed 42, action cutoff 0.5;
- Cùng budget / cùng heldout / cùng seed: B=20, seed 42; chưa so sánh heldout.

**Bảng 1 · Baseline vs PoC & hard slices** — dán output của
`python -m src.compare reports/metrics_baseline.json reports/metrics_poc.json`:

| Metric (unit) | Baseline | PoC | Δ & chú thích |
|---|---|---|---|
| Primary: Defect Recall@20| 0.6364 | 0.8182 | +0.1818; higher; không official |
| Hard-slice: | 0.1000 | 0.2000 | +0.1000; lower ; N=16 |
| Review@B / miss / FP | P@20 0.7000; TP=14, FN=8, FP=6 | P@20 0.9000; TP=18, FN=4, FP=2 | B=20 |
| Runtime / 100 items | | | |

## 05 · Evaluation protocol

- Công thức primary metric, đơn vị đánh giá, TP/FP/FN, denominator: Recall@20=TP/số defect; Precision@20=TP/20; đơn vị ảnh.
- Threshold / budget B / K và lúc chốt:
- Độ bất định (CI/bootstrap/variance) khi N nhỏ: 
- Lệnh evaluate chính xác: `python -m src.evaluate --track t__ --pred ... --out ...`

## 06 · Result slices

| Slice | N | Baseline | PoC | Ghi chú |
|---|---|---|---|---|
| | | | | |
| | | | | |

- Failure case 1 (ảnh/metadata + root cause): ____
- Failure case 2: ____
- Latency / compute (máy, CPU, ms/item): ____

## 07 · Human & decision

- Review queue (file, B, cách ưu tiên): `reports/review_queue.csv`, B=20; ưu tiên score giảm dần.
- Cách override + audit log (`out/audit_log.jsonl`): reviewer dùng approve/reject/edit; edit ghi KEEP/RECAPTURE.
- Decision policy + `reason_code` + owner: HOLD khi pending/escalate; REWORK khi có severity-2 recapture; run hiện tại REWORK, `SEVERITY_2_RECAPTURE_REQUIRED`, owner `Tran Duc Hai`.
- Khi nào HOLD/REWORK thay vì tự động release: HOLD nếu còn review/escalate; REWORK nếu reviewer xác nhận ảnh không dùng được cần chụp lại; không tự release khi chưa review xong.

## 08 · Reproduce & tests

```bash
make setup          # hoặc: python -m venv .venv && pip install -r requirements.txt
python -m src.main --input fixtures/<track> --config configs/lab.yaml --output out/ --offline --seed 42
python -m src.evaluate --track t__ --pred out/<file> --out reports/metrics_poc.json
pytest -q
```

- Output của `pytest -q`: PASS, exit 0
- Máy / CPU / OS / Python: Windows 11 · Intel64 Family 6 Model 142 Stepping 12 · Python 3.14.6 · git sha ghi git sha: ____
## 09 · Limitations & ethics

- Rủi ro pháp lý / PII / license: chỉ dùng fixture synthetic CC0-1.0, không có ảnh người thật.
- Điều chưa biết; false alarms; nếu scale 10× thì sao: chưa xác thực trên ảnh; false alarm theo dev slices được ghi ở trên; chưa benchmark scale 10×.
- Không tuyên bố "đạt GDPR / luật Việt Nam" từ checklist tự động; giả định cần DPO/legal xác nhận: domain owner xác nhận ngưỡng ảnh không dùng được.
- Kế hoạch cải tiến: 
## 10 · Team contribution

**Bảng 2 · Individual contribution evidence** (đối chiếu với `git shortlog -sne --all` và `reports/team_contributions.csv`)

| Học viên | Git author/email | Module / vai trò | Commit SHA / diff chứng minh | Trình bày? |
|---|---|---|---|---|
| Trần Đức Hải| | Pipeline T02, review, report | | |
| Member 2 | | Data + GT | | |
| Member 3 | | Core algorithm | | |
| Member 4 | | Metric + tests | | |
| Member 5 | | Review queue + report | | |
| Member 6 (nếu có) | | Research / ablation / debate | | |

## 11 · References

| URL | Ngày xem | 1–2 dòng ý nghĩa (không chép nguyên văn) |
|---|---|---|
| | | |

---

**Bảng 3 · Checklist chốt khi nộp**

- [ ] Link repo và tag cuối cùng trong README; input mẫu không chứa PII thật.
- [ ] Đúng 1 lệnh run, 1 lệnh evaluate, 1 lệnh tests; không cần download khi chạy.
- [ ] Có baseline/PoC metrics, ≥1 failure case, reviewer evidence, decision log.
- [ ] Có license/source provenance, data splits, seed, offline environment, GT/eval N.
- [ ] Mỗi thành viên có ≥1 commit thực chất và được nêu minh bạch trong bảng trên.
