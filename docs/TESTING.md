# Kiểm thử hệ thống quỹ chung

## 1. Bộ test tự động

Tại thư mục gốc dự án, sau khi kích hoạt venv và cài `requirements.txt`:

```bash
python -m unittest discover -s tests
```

Trên macOS ngoài venv, thay `python` bằng `python3`.

| File | Số test | Phạm vi |
|---|---:|---|
| `tests/test_bully.py` | 16 | Bầu chọn trên mạng giả lập, heartbeat, chuyển vai trò, dừng node, bỏ công bố ID thấp đến trễ |
| `tests/test_lock_flow.py` | 10 | Xin/cấp/trả khoá, FIFO, huỷ đơn khi đổi điều phối viên, phát hiện mất phản hồi |
| `tests/test_balance_sync.py` | 8 | Thông báo số dư sau giao dịch thành công, bỏ qua giao dịch lỗi, retry gửi, kết nối đọc DB riêng và TCP khi rảnh |
| `tests/test_startup.py` | 6 | Bully chạy trước khi kết nối xong, thử lại peer offline, bỏ qua peer đã kết nối, đóng node dừng thử lại và đường gửi ngược |
| `tests/test_membership.py` | 7 | TCP thật: tự kết nối theo cấu hình không cần join, node mở sau, ID cao/thấp gia nhập, phát hiện mesh, bầu lại, kết nối lại và log UI hai phía |
| `tests/test_concurrent_access.py` | 3 | Nhiều luồng xin quyền đồng thời chỉ cấp một node, giữ FIFO khi trả quyền đồng thời có yêu cầu mới, bỏ qua trả quyền sai người |
| `tests/test_transaction_delay.py` | 7 | Nạp/rút đếm ngược trước khi ghi, thay đổi/tắt delay, đóng khi đang chờ, rút vượt số dư, FIFO hoạt động trong lúc delay, GRANT lặp và hai thao tác đồng thời |

Kết quả đã kiểm tra trên Windows: **57 test thành công**. Bộ test không mở giao diện và không truy cập DB thật; test mạng dùng `socketpair()` và TCP localhost. Đếm ngược được kiểm tra bằng đồng hồ giả để không phải chờ 3 giây trong mỗi test; riêng kiểm thử giao diện Tkinter ẩn với DB giả lập đã xác nhận cả nạp/rút hiển thị đếm 3 → 2 → 1 → 0 và bật lại hai nút sau giao dịch. Chưa xác nhận Supabase và kết nối ứng dụng đầy đủ trên nhiều máy qua ZeroTier.

## 2. Điều kiện trước khi thử nhiều máy

- Các máy dùng cùng phiên bản mã nguồn và danh sách IP/port các node cần kết nối trong `config.json`. Mỗi máy chỉ cần chạy `--id`; TCP tự nối các địa chỉ cấu hình trong nền.
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
| Mở node 0 trước, sau đó mở node 2, mỗi máy chỉ dùng `--id` | Node 0 làm điều phối viên khi chạy riêng; TCP tự kết nối, hai phía hiện log kết nối, cả hai theo node 2 khi vai trò ổn định |
| Mở node 2 trước, sau đó mở node 0, mỗi máy chỉ dùng `--id` | Node 0 tự kết nối và theo điều phối viên 2; máy cũ không cần khởi động lại |
| Node 0 nạp 100000 | DB tăng thành `B + 100000`, có một bản ghi DEPOSIT; cả hai giao diện cập nhật khi nhận thông báo và đọc DB thành công |
| Node 2 rút 50000 | Nếu đủ tiền, DB còn `B + 50000` và có một bản ghi WITHDRAW |
| Hai máy cùng nạp 100000 và 200000 | Xin quyền lần lượt; tổng số dư tăng 300000, có đủ hai giao dịch |
| Bấm nạp/rút, rồi bấm giao dịch ở node khác trong 3 giây | Node được cấp quyền hiển thị đếm ngược; node còn lại hiện xếp hàng. Sau giao dịch đầu, node kế tiếp mới bắt đầu đếm. Hai nút trên mỗi node được mở lại khi giao dịch của node đó kết thúc |
| Nhập 0, số âm hoặc chữ | Giao diện từ chối, không ghi giao dịch |
| Rút vượt số dư | Giao dịch bị từ chối; vẫn trả khoá để giao dịch tiếp theo chạy được |
| Để ứng dụng rảnh hơn 5 giây, sau đó giao dịch | Luồng nhận TCP vẫn hoạt động, máy khác nhận được thông báo số dư |
| Đóng node 2 khi không có giao dịch đang chạy | Node 0 phát hiện mất điều phối viên và bầu lại; khi ổn định, node 0 làm điều phối viên |
| Mở lại node 2 chỉ với `--id 2` | TCP tự kết nối lại, node 2 trở thành điều phối viên, node 0 hạ vai trò |
| Đóng cửa sổ ngay trong lúc kết nối/bầu chọn | Node không tự hoạt động lại sau khi đã dừng |
| Mở hai node với cấu hình địa chỉ đúng, không truyền `--join` | Các node tự kết nối trong nền và thống nhất coordinator; không phải đợi đủ tất cả địa chỉ cấu hình online |

Để xem riêng bố cục giao diện: `python -m ui.app_window`. Chế độ này không chạy Bully, không truy cập DB và không thực hiện nạp/rút thật.

`logs/` và `__pycache__/` có thể xuất hiện lại sau khi chạy ứng dụng hoặc kiểm thử; đây là dữ liệu sinh ra, không cần có sẵn trong repo. Các script tạm trong `logs/` đã được dọn; dùng các lệnh ở trên để chạy và kiểm thử.

## 5. Giới hạn cần ghi nhận

- Node giữ khoá crash trước khi trả quyền có thể làm hàng đợi chờ vô hạn; chưa có cơ chế thu hồi khoá.
- Khi đổi điều phối viên, đơn đang chờ bị huỷ và cần bấm lại. Giao dịch đang thực thi trong lúc chuyển vai trò chưa có fencing/version bảo vệ.
- Thông báo số dư thử gửi lại một lần nếu lần gửi đầu thất bại, không lưu để phát lại; chưa có đồng bộ bù cho mọi trường hợp mất kết nối. Thay đổi DB trực tiếp ngoài ứng dụng cũng không phát thông báo.
- Worker TCP tự kết nối lại các peer cấu hình khi đường gửi mất, nhưng không bảo đảm phục hồi mọi trường hợp chia cắt mạng hoặc giao dịch đang chạy. Địa chỉ cấu hình sai hoặc peer không truy cập được có thể khiến mỗi nhóm tạm tự làm coordinator.
- Ghi lại lỗi terminal, vai trò/điều phối viên trên từng cửa sổ và file `logs/node_<id>.log` để đối chiếu. Không chia sẻ `.env` hoặc mật khẩu trong báo cáo.
