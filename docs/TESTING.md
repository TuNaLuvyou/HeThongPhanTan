# Kiểm thử hệ thống quỹ chung

## 1. Bộ test tự động

Tại thư mục gốc dự án, sau khi kích hoạt venv và cài `requirements.txt`:

```bash
python -m unittest discover -s tests
```

Trên macOS ngoài venv, thay `python` bằng `python3`.

| File | Số test | Phạm vi |
|---|---:|---|
| `tests/test_bully.py` | 15 | Bầu chọn trên mạng giả lập, heartbeat, chuyển vai trò, dừng node |
| `tests/test_lock_flow.py` | 10 | Xin/cấp/trả khoá, FIFO, huỷ đơn khi đổi điều phối viên, phát hiện mất phản hồi |
| `tests/test_balance_sync.py` | 8 | Thông báo số dư sau giao dịch thành công, bỏ qua giao dịch lỗi, retry gửi, kết nối đọc DB riêng và TCP khi rảnh |
| `tests/test_startup.py` | 4 | Kết nối trước khi bật Bully, UI không bị chặn, đóng khi đang kết nối, đường gửi ngược trên socket nhận |

Kết quả đã kiểm tra trên Windows: **37 test thành công**. Bộ test không mở giao diện và không truy cập DB thật; một số test mạng dùng `socketpair()` cục bộ. Kết quả này chưa xác nhận giao diện thực tế, Supabase và kết nối nhiều máy qua ZeroTier.

## 2. Điều kiện trước khi thử nhiều máy

- Các máy dùng cùng phiên bản mã nguồn và cùng danh sách node trong `config.json`.
- Mỗi máy có node ID riêng, IP trong cấu hình là IP ZeroTier thực tế của máy đó.
- Thiết bị đã được duyệt trong cùng mạng ZeroTier; firewall cho phép Python nhận kết nối TCP ở cổng cấu hình của node.
- Từng máy đã cài Python, Tkinter và `requirements.txt`, tạo `.env` riêng với `DATABASE_URL` trỏ cùng DB Supabase.
- Đã chạy `db/schema.sql` để tạo dữ liệu cần thiết. Không chạy các lệnh xoá/reset dữ liệu nếu chưa chủ động muốn làm lại quỹ.

Ví dụ đang dùng: Windows node `0`, cổng `3000`; macOS node `2`, cổng `3002`. Các node khác hiện là localhost, chưa đại diện cho máy từ xa.

## 3. Chạy thử

Windows:

```powershell
python main.py --id 0
```

macOS:

```bash
python3 main.py --id 2
```

Sau khi Mac đã chạy, có thể kiểm tra cổng từ Windows:

```powershell
Test-NetConnection 10.130.28.199 -Port 3002
```

Mong đợi `TcpTestSucceeded: True`. Điều này xác nhận cổng TCP truy cập được; vẫn cần đối chiếu điều phối viên trên hai giao diện.

## 4. Kịch bản demo

Gọi số dư trước khi thử là `B`. Dùng giá trị thực tế trong DB, không giả định quỹ luôn còn 10 triệu.

| Thao tác | Kết quả mong đợi |
|---|---|
| Mở node 0 trước, sau đó mở node 2 | Node 0 có thể làm điều phối viên khi chạy riêng; khi node 2 tham gia và bầu chọn xong, cả hai theo node 2 |
| Mở node 2 trước, sau đó mở node 0 | Node 0 theo điều phối viên 2; máy cũ gửi ngược heartbeat/cấp quyền được cho máy mới |
| Node 0 nạp 100000 | DB tăng thành `B + 100000`, có một bản ghi DEPOSIT; cả hai giao diện cập nhật khi nhận thông báo và đọc DB thành công |
| Node 2 rút 50000 | Nếu đủ tiền, DB còn `B + 50000` và có một bản ghi WITHDRAW |
| Hai máy cùng nạp 100000 và 200000 | Xin quyền lần lượt; tổng số dư tăng 300000, có đủ hai giao dịch |
| Nhập 0, số âm hoặc chữ | Giao diện từ chối, không ghi giao dịch |
| Rút vượt số dư | Giao dịch bị từ chối; vẫn trả khoá để giao dịch tiếp theo chạy được |
| Để ứng dụng rảnh hơn 5 giây, sau đó giao dịch | Luồng nhận TCP vẫn hoạt động, máy khác nhận được thông báo số dư |
| Đóng node 2 khi không có giao dịch đang chạy | Node 0 phát hiện mất điều phối viên và bầu lại; khi ổn định, node 0 làm điều phối viên |
| Mở lại node 2 | Node 2 bầu chọn và trở thành điều phối viên, node 0 hạ vai trò |
| Đóng cửa sổ ngay trong lúc kết nối/bầu chọn | Node không tự hoạt động lại sau khi đã dừng |

Để xem riêng bố cục giao diện: `python -m ui.app_window`. Chế độ này không chạy Bully, không truy cập DB và không thực hiện nạp/rút thật.

## 5. Giới hạn cần ghi nhận

- Node giữ khoá crash trước khi trả quyền có thể làm hàng đợi chờ vô hạn; chưa có cơ chế thu hồi khoá.
- Khi đổi điều phối viên, đơn đang chờ bị huỷ và cần bấm lại. Giao dịch đang thực thi trong lúc chuyển vai trò chưa có fencing/version bảo vệ.
- Thông báo số dư thử gửi lại một lần nếu lần gửi đầu thất bại, không lưu để phát lại; chưa có đồng bộ bù cho mọi trường hợp mất kết nối. Thay đổi DB trực tiếp ngoài ứng dụng cũng không phát thông báo.
- Kết nối trước bầu chọn và đường gửi ngược hỗ trợ khởi động/tham gia mạng; không bảo đảm phục hồi mọi trường hợp đứt mạng hoặc chia cắt mạng.
- Ghi lại lỗi terminal, vai trò/điều phối viên trên từng cửa sổ và file `logs/node_<id>.log` để đối chiếu. Không chia sẻ `.env` hoặc mật khẩu trong báo cáo.
