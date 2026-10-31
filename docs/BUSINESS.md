# Nghiệp vụ Hệ thống (BUSINESS)

## 1. Bài toán
- 6 máy trạm cùng nạp/rút một quỹ chung.
- Nếu nhiều máy ghi cùng lúc thì mất cập nhật, nên cần loại trừ tương hỗ: tại một thời điểm chỉ một node được truy cập số dư.

## 2. Tác nhân
- **Node**: Người dùng trên một máy trạm, định danh bằng ID từ 0 đến 5.
- **Coordinator**: Vai trò tạm thời của một node (node có ID lớn nhất còn sống, được bầu bằng giải thuật Bully; cấp quyền truy cập cho các node khác).

## 3. Quỹ chung
- Số dư lưu trong CSDL (Supabase Postgres).
- Số dư ban đầu: 10,000,000 (`config.json`: `shared_balance_initial`).
- Chỉ node đang được coordinator cấp quyền mới được đọc và ghi số dư.

## 4. Chức năng cho người dùng
- **Xem trạng thái node**: Vai trò (Node bình thường / Coordinator), ID coordinator hiện tại, tự động cập nhật.
- **Nạp tiền**: Gửi yêu cầu xin quyền và nạp tiền vào quỹ.
- **Rút tiền**: Gửi yêu cầu xin quyền và rút tiền từ quỹ.
- **Xem History Log**: Theo dõi tiến trình bầu chọn, xin/được cấp/trả quyền, và các giao dịch.

## 5. Quy tắc nghiệp vụ
- **Kiểm tra số dư**: Không rút vượt quá số dư hiện có.
- **Định dạng số tiền**: Số tiền phải là số nguyên dương.
- **Ghi vết giao dịch**: Mọi giao dịch được ghi lại (node, loại DEPOSIT/WITHDRAW, số tiền, thời điểm).
- **Thứ tự cấp quyền**: Các node chờ quyền được phục vụ theo thứ tự đến trước được cấp trước (FIFO).

## 6. Khi Coordinator lỗi
- Hệ thống tự phát hiện và bầu coordinator mới bằng giải thuật Bully.
- Người dùng thấy thông báo trên giao diện.

## 7. Ngoài phạm vi
- Web app.
- Đăng nhập/phân quyền người dùng.
- Phân mảnh mạng (hai coordinator cùng lúc).
- Mất gói tin giữa đường.
