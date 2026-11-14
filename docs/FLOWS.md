# Luồng Hoạt động (FLOWS)

## 1. Khởi động node
1. Đọc tham số `--id` và cấu hình từ `config.json`.
2. Tự bind port của mình theo cấu hình.
3. Bắt đầu bầu chọn: gửi `ELECTION` cho các node có ID lớn hơn.
4. Nếu không ai trả lời `ANSWER`: tự làm coordinator và gửi `COORDINATOR` cho tất cả các node.
5. Nếu có node trả lời `ANSWER`: chờ nhận thông điệp `COORDINATOR`.

## 2. Bully khi coordinator lỗi
1. Phát hiện bằng hai cách:
   - (a) Vòng canh của node phụ: quá `COORDINATOR_SILENCE_TIMEOUT` (6 giây, xem TECHS mục 9b) không nhận được dấu hiệu coordinator còn sống (không `HEARTBEAT`, không `COORDINATOR`) thì ghi log `COORDINATOR_FAIL` và tự bắt đầu bầu lại. Việc một peer không trả `HEARTBEAT_ACK` chỉ có nghĩa peer đó offline, không được kích hoạt bầu lại.
   - (b) Gửi `REQUEST_LOCK` mà quá `LOCK_REPLY_TIMEOUT` (10 giây) không nhận `GRANT_LOCK` lẫn `LOCK_QUEUED` (hiện cảnh báo: "Điều phối viên không phản hồi, bắt đầu bầu chọn") thì kích hoạt bầu lại. Gửi `REQUEST_LOCK` mà kết nối thất bại cũng bầu lại ngay.
2. Node phát hiện gửi `ELECTION` chỉ cho các node có ID lớn hơn.
3. Node nhận `ELECTION`: chỉ node có ID LỚN HƠN node khởi động mới trả `ANSWER` (kèm bắt đầu bầu cử của chính nó); node có ID nhỏ hơn im lặng. Đây là quy tắc Bully cổ điển — bản cũ đòi `sender_id > self.node_id` nên không ai trả lời và mọi node đều tự phong coordinator cùng lúc.
4. Node đang là coordinator mà nhận `ELECTION` từ node ID nhỏ hơn thì trả `ANSWER` kèm `COORDINATOR` để node khởi động theo lại ngay, tránh hai coordinator cùng tại vị.
5. Node khởi động không nhận `ANSWER` nào trong `ELECTION_ANSWER_TIMEOUT` (5 giây) trở thành coordinator và broadcast `COORDINATOR` cho tất cả các node.
6. Node đã trả/nhận `ANSWER` (đã nhường) chuyển về trạng thái chờ và theo coordinator khi nhận `COORDINATOR`.
7. Node đã chủ động tắt cửa sổ thì từ chối nhận vai trò: bỏ qua thông báo `COORDINATOR`, ghi log `IGNORE_AFTER_STOP`; vòng canh cũng đã dừng nên node không tự phát hiện lỗi hay khởi động bầu cử nữa.

## 3. Node ID cao hơn tham gia mạng
1. Node mới khởi động bắt đầu bầu cử: gửi `ELECTION` cho các node có ID lớn hơn; không có node nào cao hơn (hoặc các node đó chết) nên không nhận `ANSWER` và tự tuyên bố coordinator, broadcast `COORDINATOR` cho toàn mạng.
2. Coordinator cũ nhận `COORDINATOR` từ node có ID lớn hơn thì hạ vai trò xuống thành node bình thường (ghi log `STEP_DOWN`) và xoá trạng thái khoá: node giữ quyền + hàng đợi FIFO bị đặt về rỗng (`BankService.reset_lock()`, log `LOCK_RESET`) vì hàng đợi cũ không còn ai trả lời. Coordinator mới bắt đầu với hàng đợi trống.
3. Ngược lại, coordinator đang tại vị nhận `ELECTION` từ node ID thấp hơn thì KHÔNG hạ vai trò: trả `ANSWER` + `COORDINATOR` cho node kia theo lại (TECHS mục 6, FLOWS mục 2.4).
4. Đơn xin quyền còn treo ở coordinator cũ: node đang chờ phát hiện đổi coordinator thì đơn bị huỷ và UI báo bấm lại; việc tự động gửi lại `REQUEST_LOCK` cho coordinator mới CHƯA có — còn chờ chốt ở TECHS mục 9d.

## 4. Nạp/Rút
1. Node gửi `REQUEST_LOCK` cho coordinator.
2. Coordinator cấp `GRANT_LOCK` nếu khoá đang rảnh; nếu đang có node khác giữ thì đưa node vào hàng đợi FIFO và trả `LOCK_QUEUED` ("đã nhận đơn, đang xếp hàng").
3. `GRANT_LOCK` hoặc `LOCK_QUEUED` đều được tính là coordinator đã phản hồi; node ngừng đồng hồ phát hiện coordinator lỗi (FLOWS mục 2b). Thiếu cả hai phản hồi quá `LOCK_REPLY_TIMEOUT` (10 giây) thì node bầu lại.
4. Node được cấp quyền thực hiện: đọc số dư từ DB, mô phỏng xử lý, ghi giá trị đã tính vào DB và ghi vào bảng `transactions`.
5. Node gửi `RELEASE_LOCK` cho coordinator. Nếu bản thân node đang là coordinator thì tự giải phóng khoá trực tiếp (không gửi qua mạng cho chính mình).
6. Coordinator nhận `RELEASE_LOCK`, bỏ node giữ quyền và cấp `GRANT_LOCK` cho node kế tiếp trong hàng đợi FIFO.
7. Trường hợp node giữ quyền crash mà không gửi `RELEASE_LOCK`: code hiện chưa thu hồi khoá — các node đã nhận `LOCK_QUEUED` chờ vô hạn (CẦN CHỐT, xem TECHS mục 9c).

> **Lưu ý cho luồng 4**: Khi demo loại trừ tương hỗ phải theo trình tự đọc, chờ, ghi giá trị đã tính; không dùng câu lệnh UPDATE nguyên tử kiểu `balance = balance + x` vì nó tự tránh mất cập nhật và làm khoá mất tác dụng khi demo.

## 5. Rút vượt số dư
1. Sau khi nhận quyền và đọc số dư từ DB, phát hiện số dư không đủ.
2. Từ chối giao dịch, hiển thị báo lỗi trên UI.
3. Vẫn gửi `RELEASE_LOCK` về cho coordinator để giải phóng khoá.

## 6. Node giữ quyền bị crash và đổi coordinator giữa chừng
- **Trạng thái**: CẦN CHỐT (xem TECHS.md mục 9c, 9d).
