# Nghiệp vụ Hệ thống (BUSINESS)

## 1. Bài toán
- Hệ thống được thiết kế cho 6 máy trạm cùng nạp/rút một quỹ chung; không cần mở đủ 6 máy mới hoạt động.
- Nếu nhiều máy ghi cùng lúc thì mất cập nhật, nên cần loại trừ tương hỗ: tại một thời điểm chỉ một node được truy cập số dư.

## 2. Tác nhân
- **Node**: Người dùng trên một máy trạm, định danh bằng ID từ 0 đến 5.
- **Coordinator**: Vai trò tạm thời của một node (node có ID lớn nhất còn hoạt động trong nhóm đã kết nối, được bầu bằng Bully; cấp quyền truy cập cho các node khác).

## 2a. Khởi động và gia nhập
- Node mở server và chạy Bully ngay. Chưa biết thành viên nào thì tự làm coordinator.
- TCP tự kết nối các IP/port trong `config.json` ở nền ngay khi mở ứng dụng, thử lại peer chưa mở sau 5 giây; không cần tham số `--join` và không chờ các lần kết nối hoàn tất trước khi chạy Bully.
- Các thành viên trao đổi ID và địa chỉ, rồi thống nhất coordinator theo ID. Chuyển vai trò trong ứng dụng đang chạy, không cần khởi động lại.
- Các máy cần có địa chỉ của nhau trong cấu hình và kết nối TCP thông suốt. Trước khi thống nhất vai trò, các node chưa kết nối có thể tạm thời tự làm coordinator; cần đợi vai trò ổn định trước khi giao dịch chung DB.

## 3. Quỹ chung
- Số dư lưu trong CSDL (Supabase Postgres).
- Số dư ban đầu: 10,000,000, được khởi tạo bởi `db/schema.sql`. `shared_balance_initial` trong cấu hình chưa được code dùng để thay đổi số dư DB; thay giá trị cấu hình không reset quỹ.
- Trong giao dịch nạp/rút, node phải được coordinator cấp quyền trước khi đọc, tính và ghi số dư. Đọc số dư chỉ để hiển thị trên giao diện không xin khoá và không thay đổi quỹ.

## 4. Chức năng cho người dùng
- **Xem trạng thái node**: Vai trò (Node bình thường / Coordinator), ID coordinator hiện tại, tự động cập nhật.
- **Nạp tiền**: Gửi yêu cầu xin quyền và nạp tiền vào quỹ.
- **Rút tiền**: Gửi yêu cầu xin quyền và rút tiền từ quỹ.
- **Xem History Log**: Theo dõi tiến trình bầu chọn, xin/được cấp/trả quyền, và các giao dịch.
- **Đồng bộ số dư giữa các máy**: sau giao dịch thành công, node thông báo để các node khác đọc lại số dư từ DB. Node không nhận được thông báo có thể còn hiển thị số dư cũ; khi mở lại ứng dụng sẽ đọc số dư hiện tại.
- **Giao diện tối giản bằng tiếng Việt**: số dư được làm nổi bật, hiển thị vai trò và điều phối viên; nhật ký có thời gian và màu phân biệt thành công/lỗi.
- **Quan sát giao dịch**: sau khi được cấp quyền và đọc số dư, nạp/rút có thời gian mô phỏng 3 giây với đếm ngược trên giao diện. Node khác có thể xin quyền và thấy trạng thái xếp hàng; node đang giao dịch không nhận thêm thao tác nạp/rút tới khi hoàn tất.

## 5. Quy tắc nghiệp vụ
- **Kiểm tra số dư**: Không rút vượt quá số dư hiện có.
- **Định dạng số tiền**: Số tiền phải là số nguyên dương.
- **Ghi vết giao dịch**: Giao dịch thành công được ghi lại (node, loại DEPOSIT/WITHDRAW, số tiền, thời điểm); yêu cầu bị từ chối không tạo bản ghi giao dịch.
- **Thứ tự cấp quyền**: Các node chờ quyền được phục vụ theo thứ tự đến trước được cấp trước (FIFO).
- **Cấp quyền tập trung**: node gửi `REQUEST`, coordinator trả `GRANT` nếu rảnh hoặc `QUEUED` nếu phải đợi; node thực hiện giao dịch rồi gửi `RELEASE`.

## 6. Khi Coordinator lỗi
- Hệ thống tự phát hiện và bầu coordinator mới bằng giải thuật Bully.
- Người dùng thấy thông báo trên giao diện.
- Khi đổi coordinator, đơn xin quyền đang chờ bị huỷ và người dùng cần bấm lại. Bầu lại coordinator chưa giải quyết đầy đủ giao dịch đang chạy hoặc node giữ quyền bị crash; xem [giới hạn kiểm thử](TESTING.md#5-giới-hạn-cần-ghi-nhận).

## 7. Ngoài phạm vi
- Web app.
- Đăng nhập/phân quyền người dùng.
- Phân mảnh mạng (hai coordinator cùng lúc).
- Mất gói tin giữa đường.
