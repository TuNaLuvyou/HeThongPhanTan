# Quy tắc Dự án (RULES)

## 1. Phân chia trách nhiệm
- Mỗi thành viên chỉ sửa file thuộc module của mình.
- Muốn đổi giao thức hoặc config phải qua trưởng nhóm và cập nhật vào `docs/TECHS.md`.

## 2. Tiêu chuẩn mã nguồn
- Sử dụng Python 3.9+, tuân thủ theo chuẩn PEP8.
- Tên biến và hàm bằng tiếng Anh, comment giải thích bằng tiếng Việt.
- Code đơn giản, dễ đọc, dễ giải thích khi bảo vệ.
- Không dùng thư viện ngoài, ngoại trừ những thư viện đã có trong `requirements.txt`.

## 3. Cấu hình và bảo mật
- Không hard-code IP, port, mật khẩu: lấy từ `config.json` hoặc `.env`.
- Tuyệt đối không commit file `.env`.

## 4. Xử lý mạng và Thread
- Mọi socket phải đặt timeout.
- Mọi thread phụ bắt exception và ghi log.

## 5. Cập nhật giao diện Tkinter
- Chỉ cập nhật giao diện từ luồng chính (dùng `queue` hoặc `after`), không cập nhật trực tiếp từ thread mạng.

## 6. Quy định ghi Log
- Mỗi sự kiện một dòng gồm: thời gian, ID node, sự kiện.
- Ghi đồng thời ra màn hình History Log và file `logs/node_<id>.log`.

## 7. Quy trình Git
- Tạo nhánh theo từng module: `feature/bully`, `feature/network`, `feature/bank`, `feature/ui`.
- Commit message có dạng: `[module] mô tả`.
- Chỉ trưởng nhóm merge vào nhánh `main`.

## 8. Các mục CẦN CHỐT
- Không tự quyết các mục **CẦN CHỐT**.
- Gặp tình huống chưa rõ thì ghi vào báo cáo module để cả nhóm họp chốt.
