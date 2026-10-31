# Hướng dẫn dành cho AI Agent (AGENTS)

Tài liệu hướng dẫn dành cho AI Agent khi tham gia làm việc trong repo:

## 1. Thứ tự đọc tài liệu
Trước khi thực hiện bất kỳ nhiệm vụ nào, đọc tài liệu theo đúng thứ tự:
1. `docs/BUSINESS.md`
2. `docs/TECHS.md`
3. `docs/FLOWS.md`
4. `docs/RULES.md`
5. `README.md`

## 2. Phạm vi can thiệp
- Xác định module bạn được giao (hỏi người dùng nếu chưa rõ) và chỉ chỉnh sửa file thuộc module đó.
- Không tự đổi giao thức, config, cấu trúc thư mục.
- Không tự quyết các mục **CẦN CHỐT**, hãy nêu phương án và để nhóm chốt.

## 3. Tiêu chuẩn code
- Viết code đơn giản, comment tiếng Việt, tránh thiết kế quá phức tạp, để thành viên đọc và giải thích được.
- Không đưa mật khẩu hay chuỗi kết nối vào code hoặc commit.

## 4. Mẫu báo cáo Module
Sau khi code xong, trả lời bằng báo cáo module theo đúng mẫu sau:
1. **Đã làm gì**: Danh sách các chức năng, hàm, lớp đã lập trình.
2. **Giao diện hàm/lớp để module khác gọi**: Chi tiết interface để tích hợp.
3. **Giả định đã dùng**: Các giả định kỹ thuật (nếu có).
4. **Việc còn mở và đề xuất cho mục CẦN CHỐT**: Đóng góp ý kiến cho các mục CẦN CHỐT trong `docs/TECHS.md`.
5. **Cách chạy thử riêng module**: Hướng dẫn chạy thử nghiệm độc lập module.
