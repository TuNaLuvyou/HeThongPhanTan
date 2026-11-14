# Luồng Hoạt động (FLOWS)

## 1. Khởi động node
1. Đọc tham số `--id` và cấu hình từ `config.json`.
2. Tự bind port của mình theo cấu hình.
3. Bật Bully ngay. Chưa biết thành viên nào thì tự làm coordinator ngay, không chờ 5 giây và không chờ kết nối TCP hoàn tất.
4. Tự mở TCP tới các IP/port peer trong `config.json`, mỗi peer một worker nền. Peer chưa mở hoặc mất kết nối được thử lại sau 5 giây; peer đang kết nối được bỏ qua. Không cần `--join`. Khi TCP nối được, tự trao `JOIN`/`JOIN_ACK` (ID, IP, port của mình và các thành viên đã biết).
5. Từ các địa chỉ được giới thiệu, kết nối tới các thành viên khác. Coordinator gặp ID thấp hơn gửi `COORDINATOR`; gặp ID cao hơn gửi `ELECTION` để node đó công bố/bầu chọn. Node thường chỉ yêu cầu bầu chọn khi chưa biết coordinator hoặc gặp ID cao hơn coordinator, không huỷ đơn chờ chỉ vì có node ID thấp hơn gia nhập.
6. Bầu chọn và heartbeat dùng danh sách thành viên đã phát hiện trong lúc chạy; địa chỉ trong cấu hình là đầu mối kết nối, không phải bằng chứng online. Node mở sau được nhận biết qua worker thử lại và bắt tay, không cần khởi động lại các node cũ.

Khi nhận gói tin đầu tiên có trường `from`, server lưu kết nối nhận làm đường gửi ngược nếu chưa có kết nối tới node đó. Vì vậy node đang chạy có thể gửi heartbeat/cấp quyền tới node vừa mở mà không cần khởi động lại. Nếu đóng ứng dụng khi đang kết nối hoặc bầu chọn, node không được khởi động Bully lại hay tự nhận điều phối viên sau khi đã dừng.

## 2. Bully khi coordinator lỗi
1. Phát hiện bằng hai cách:
   - (a) Vòng canh của node phụ: quá `COORDINATOR_SILENCE_TIMEOUT` (6 giây, xem TECHS mục 9b) không nhận được dấu hiệu coordinator còn sống (không `HEARTBEAT`, không `COORDINATOR`) thì ghi log `COORDINATOR_FAIL` và tự bắt đầu bầu lại. Việc một peer không trả `HEARTBEAT_ACK` chỉ có nghĩa peer đó offline, không được kích hoạt bầu lại.
   - (b) Gửi `REQUEST` mà quá `REQUEST_REPLY_TIMEOUT` (10 giây) không nhận `GRANT` lẫn `QUEUED` (hiện cảnh báo: "Điều phối viên không phản hồi, bắt đầu bầu chọn") thì kích hoạt bầu lại. Gửi `REQUEST` mà kết nối thất bại cũng bầu lại ngay.
2. Node phát hiện gửi `ELECTION` chỉ cho các node có ID lớn hơn.
3. Node nhận `ELECTION` chỉ trả `ANSWER` nếu ID của mình lớn hơn ID người gửi. `ANSWER` là phản hồi tương đương `OK`; code không có thông điệp tên `OK`. Nếu chưa có coordinator đang phản hồi, node nhận tiếp tục bầu chọn bằng cách gửi `ELECTION` tới các ID cao hơn mình đã biết.
   - Nếu node nhận vẫn đang theo một coordinator vừa phản hồi trong 6 giây gần nhất, nó trả `ANSWER` và nhờ coordinator xác nhận lại qua `ELECTION`, giữ nguyên coordinator và đơn xin khoá đang chờ. Node mới biết coordinator qua kết nối thành viên được giới thiệu khi gia nhập.
4. Node đang là coordinator mà nhận `ELECTION` từ node ID nhỏ hơn thì trả `ANSWER` kèm `COORDINATOR` để node khởi động theo lại ngay, tránh hai coordinator cùng tại vị.
5. Có thành viên ID cao hơn nhưng không nhận `ANSWER` nào sau `ELECTION_ANSWER_TIMEOUT` (5 giây) thì tự làm coordinator. Không có thành viên ID cao hơn thì nhận vai trò ngay. Node thắng gửi `COORDINATOR` tới tất cả thành viên đã biết, gồm cả ID thấp hơn.
6. Node nhận `ANSWER` khi đang bầu chọn thì nhường quyền, về `IDLE` và chờ `COORDINATOR`; trả `ANSWER` không có nghĩa là nhường quyền. Nếu chờ công bố quá 6 giây không có dấu hiệu coordinator, watchdog bầu lại. Khi đã `ELECTING` hoặc `COORDINATOR`, lời gọi khởi động bầu chọn không tạo thêm cuộc bầu chọn mới.
7. Node đã chủ động tắt cửa sổ thì từ chối nhận vai trò: bỏ qua thông báo `COORDINATOR`, ghi log `IGNORE_AFTER_STOP`; vòng canh cũng đã dừng nên node không tự phát hiện lỗi hay khởi động bầu cử nữa.
8. Khi đã theo một coordinator ID cao hơn, bỏ qua công bố ID thấp đến trễ từ vòng gia nhập trước. Khi watchdog thực sự bầu lại, coordinator cũ được xoá để có thể nhận node ID thấp hơn thay thế.

## 3. Node ID cao hơn tham gia mạng
1. Node mới mở với `--id`, TCP tự kết nối các địa chỉ trong cấu hình ở nền. Các node trao địa chỉ, so ID và thống nhất coordinator có ID lớn nhất trong nhóm. Không có thành viên ID cao hơn thì tự làm coordinator ngay; nếu có, gửi `ELECTION` và chờ tối đa 5 giây để nhận `ANSWER`.
2. Coordinator cũ nhận `COORDINATOR` từ node có ID lớn hơn thì hạ vai trò xuống thành node bình thường (ghi log `STEP_DOWN`) và xoá trạng thái khoá: node giữ quyền + hàng đợi FIFO bị đặt về rỗng (`BankService.reset_lock()`, log `ACCESS_RESET`) vì hàng đợi cũ không còn ai trả lời. Coordinator mới bắt đầu với hàng đợi trống.
3. Ngược lại, coordinator đang tại vị nhận `ELECTION` từ node ID thấp hơn thì KHÔNG hạ vai trò: trả `ANSWER` + `COORDINATOR` cho node kia theo lại (TECHS mục 6, FLOWS mục 2.4).
4. Đơn xin quyền còn treo ở coordinator cũ: node đang chờ phát hiện đổi coordinator thì đơn bị huỷ và UI báo bấm lại; việc tự động gửi lại `REQUEST` cho coordinator mới CHƯA có — còn chờ chốt ở TECHS mục 9d.

## 4. Nạp/Rút
1. Node gửi `REQUEST` cho coordinator.
2. Coordinator cấp `GRANT` nếu khoá đang rảnh; nếu đang có node khác giữ thì đưa node vào hàng đợi FIFO và trả `QUEUED` ("đã nhận đơn, đang xếp hàng").
   - Coordinator dùng mutex nội bộ để kiểm tra/cập nhật người giữ quyền và FIFO; nhả mutex ngay sau đó, không giữ suốt giao dịch. Nhiều yêu cầu đến đồng thời chỉ có một node được cấp quyền ngay; thứ tự FIFO theo thứ tự vào hàng đợi, không theo ID.
3. `GRANT` hoặc `QUEUED` đều được tính là coordinator đã phản hồi; node ngừng đồng hồ phát hiện coordinator lỗi (FLOWS mục 2b). Thiếu cả hai phản hồi quá `REQUEST_REPLY_TIMEOUT` (10 giây) thì node bầu lại.
4. Node được cấp quyền tự đọc số dư từ DB, tính giá trị mới, đếm ngược 3 → 2 → 1 → 0 giây rồi mới ghi số dư và bảng `transactions`. Nhãn giao dịch hiển thị loại nạp/rút, số tiền và thời gian còn lại. Khi chỉ đang xếp hàng thì hiển thị chờ quyền, chưa đếm ngược. Hai nút nạp/rút của node đó bị vô hiệu hoá tới khi trả quyền hoặc huỷ đơn chờ; các node khác vẫn bấm xin quyền bình thường.
5. Node gửi `RELEASE` cho coordinator. Nếu bản thân node đang là coordinator thì tự giải phóng khoá trực tiếp (không gửi qua mạng cho chính mình).
6. Coordinator nhận `RELEASE`, bỏ node giữ quyền và cấp `GRANT` cho node kế tiếp trong hàng đợi FIFO.
7. Trường hợp node giữ quyền crash mà không gửi `RELEASE`: code hiện chưa thu hồi khoá — các node đã nhận `QUEUED` chờ vô hạn (CẦN CHỐT, xem TECHS mục 9c).

Sau giao dịch thành công và trả khoá, nút gửi `BALANCE_CHANGED` tới các nút khác. Nút nhận đọc lại số dư từ DB trong luồng nền, rồi cập nhật giao diện qua hàng đợi. Giao dịch thất bại không gửi thông báo; các nút nhận không gửi tiếp thông báo này.

> **Lưu ý cho luồng 4**: Code dùng trình tự đọc, tính, chờ mô phỏng, ghi giá trị đã tính; không dùng UPDATE kiểu `balance = balance + x`. Thời gian chờ lấy từ `TRANSACTION_DELAY` trong `main.py`; mặc định 3 giây, đặt 0 để tắt. Chờ chạy trong worker, không chặn Tkinter, heartbeat hoặc mutex quản lý FIFO. Chưa có công tắc tắt điều phối.

## 5. Rút vượt số dư
1. Sau khi nhận quyền và đọc số dư từ DB, phát hiện số dư không đủ.
2. Từ chối giao dịch, hiển thị báo lỗi trên UI.
3. Vẫn gửi `RELEASE` về cho coordinator để giải phóng khoá.

## 6. Node giữ quyền bị crash và đổi coordinator giữa chừng
- **Trạng thái**: CẦN CHỐT (xem TECHS.md mục 9c, 9d).
