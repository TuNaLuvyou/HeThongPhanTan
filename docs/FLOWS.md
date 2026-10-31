# Luồng Hoạt động (FLOWS)

## 1. Khởi động node
1. Đọc tham số `--id` và cấu hình từ `config.json`.
2. Tự bind port của mình theo cấu hình.
3. Bắt đầu bầu chọn: gửi `ELECTION` cho các node có ID lớn hơn.
4. Nếu không ai trả lời `ANSWER`: tự làm coordinator và gửi `COORDINATOR` cho tất cả các node.
5. Nếu có node trả lời `ANSWER`: chờ nhận thông điệp `COORDINATOR`.

## 2. Bully khi coordinator lỗi
1. Phát hiện bằng hai cách:
   - (a) Heartbeat không được phản hồi.
   - (b) Gửi `REQUEST_LOCK` không được phản hồi (hiện cảnh báo: "Điều phối viên không phản hồi, bắt đầu bầu chọn").
2. Node phát hiện gửi `ELECTION` chỉ cho các node có ID lớn hơn.
3. Node nhận `ELECTION` trả `ANSWER` rồi tự bắt đầu bầu cử tiếp.
4. Node không nhận được `ANSWER` nào trở thành coordinator mới và gửi `COORDINATOR` cho tất cả các node.

## 3. Node ID cao hơn tham gia mạng
1. Node mới tham gia mạng và bắt đầu bầu cử.
2. Nếu thắng cử, gửi thông điệp `COORDINATOR` cho toàn mạng.
3. Coordinator cũ nhận thông điệp, hạ vai trò xuống thành node bình thường và xoá trạng thái khoá.

## 4. Nạp/Rút
1. Node gửi `REQUEST_LOCK` cho coordinator.
2. Coordinator cấp `GRANT_LOCK` nếu đang rảnh, ngược lại đưa vào hàng đợi FIFO.
3. Node được cấp quyền thực hiện: đọc số dư từ DB, mô phỏng xử lý, ghi giá trị đã tính vào DB và ghi vào bảng `transactions`.
4. Node gửi `RELEASE_LOCK` cho coordinator.
5. Coordinator nhận `RELEASE_LOCK` và cấp quyền cho node kế tiếp trong hàng đợi.

> **Lưu ý cho luồng 4**: Khi demo loại trừ tương hỗ phải theo trình tự đọc, chờ, ghi giá trị đã tính; không dùng câu lệnh UPDATE nguyên tử kiểu `balance = balance + x` vì nó tự tránh mất cập nhật và làm khoá mất tác dụng khi demo.

## 5. Rút vượt số dư
1. Sau khi nhận quyền và đọc số dư từ DB, phát hiện số dư không đủ.
2. Từ chối giao dịch, hiển thị báo lỗi trên UI.
3. Vẫn gửi `RELEASE_LOCK` về cho coordinator để giải phóng khoá.

## 6. Node giữ quyền bị crash và đổi coordinator giữa chừng
- **Trạng thái**: CẦN CHỐT (xem TECHS.md mục 9c, 9d).
