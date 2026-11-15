# Metric contract (khai báo bắt buộc — chốt ở phút 30, trước khi chạy eval)

> Các metric chuẩn đã được cài trong `src/metrics/tracks.py`. File này là nơi nhóm **cam kết** cách dùng chúng.
> Không đổi testset, threshold hay trọng số sau khi đã thấy kết quả eval.

| Mục | Khai báo của nhóm |
|---|---|
| Track | T02 |
| Primary metric (tên đúng như evaluator in ra) | Defect Recall@B + Precision@B (B=20) |
| Tăng tốt hay giảm tốt? | higher |
| Đơn vị đánh giá (image / region / pair / release / batch / case …) | image |
| Công thức + numerator / denominator | Recall@20 = ảnh lỗi trong top-20 / tổng ảnh lỗi trong split. Precision@20 = ảnh lỗi trong top-20 / 20 |
| Cách ghép prediction ↔ GT (IoU? id? tolerance?) | image_id |
| Candidate pool cố định | 96 |
| Review budget B / K | B = 20|
| Seed | 42 |
| Threshold policy (chọn trên dev như thế nào) | Không dùng cutoff xếp hạng: score liên tục, cao hơn ưu tiên review; score = max anomaly + 0.15 × mean anomaly|
| Eval split (không dùng để tune) | 40 |
| Chỉ số lỗi xấu (miss / false alarm / false reject) | miss: ảnh lỗi ngoài top-20. False alarm: ảnh sạch trong top-20 (báo theo light). False reject: ảnh còn dùng được nhưng bị đề xuất RECAPTURE. |
| Slices sẽ báo cáo (≥2) | light: day/dusk/night (N từng slice); defect_type: blur/dark/glare/compression (N từng loại). |
| Cách báo độ bất định khi N nhỏ (Wilson CI / bootstrap) | |
| Ngày giờ chốt + người chốt | |

Nếu baseline = 0 thì ghi rõ "baseline = 0" thay vì phần trăm cải thiện.
