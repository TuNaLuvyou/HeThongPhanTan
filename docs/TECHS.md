# Kiến trúc Kỹ thuật & Giao thức (TECHS)

## 1. Công nghệ
- Python 3
- Tkinter (desktop, không web)
- Socket TCP, threading
- JSON
- Supabase Postgres
- ZeroTier (mạng ảo giữa các máy)

## 2. Kiến trúc
- Mọi node chạy cùng một mã nguồn, khác nhau ở tham số `--id`.
- Mỗi node vừa là server (lắng nghe) vừa là client (gửi).
- Coordinator không giữ số dư; số dư nằm trong DB, coordinator chỉ cấp quyền truy cập (phương pháp cấp quyền sử dụng).
- `core/network.py` vận chuyển JSON qua TCP, quản lý kết nối và trao thông tin thành viên. Dùng được qua LAN hoặc mạng ảo ZeroTier khi các máy truy cập được IP và port của nhau.
- `core/bully.py` quyết định bầu chọn và vai trò coordinator; `core/bank_service.py` giữ người đang có quyền và hàng đợi FIFO; `main.py` ghép các luồng và xử lý thông điệp.
- Khi nhóm kết nối thông suốt và bầu chọn ổn định, node ID lớn nhất còn hoạt động trong nhóm làm coordinator. Chưa có cơ chế bảo đảm một coordinator duy nhất giữa các nhóm chưa kết nối hoặc bị chia cắt.

## 3. Module và người phụ trách
- `core/network.py`: Thành viên 3 (Linux 1)
- `core/bully.py`: Thành viên 2 (macOS ARM)
- `core/bank_service.py` + `db/schema.sql`: Thành viên 4 (Linux 2)
- `ui/app_window.py`: Thành viên 5 (Windows 1)
- `main.py`: Trưởng nhóm (macOS ARM)

## 4. Cấu hình
- `config.json` chứa cấu hình các node (`id`, `ip`, `port`, `os`). Mỗi máy dùng mục ID mình để mở server và các mục khác làm địa chỉ tự kết nối TCP ở nền. Có địa chỉ trong cấu hình không có nghĩa node đó đang online.
- Port của node = giá trị trong config theo `--id`, tự bind khi khởi động.
- Bind port thất bại (port đã bị chiếm hoặc IP trong `config.json` không phải IP máy đang chạy) thì `start_node()` trả `None` và node thoát ngay kèm thông báo lỗi; không mở giao diện chạy mà không có kết nối.
- Sau khi đăng ký handler và mở UI, node bật Bully ngay. Khi chưa phát hiện thành viên nào thì tự làm coordinator ngay. Không chờ kết nối TCP hoàn tất và không chờ đủ máy.
- Runtime tạo một worker cho mỗi địa chỉ peer trong cấu hình. Worker mở TCP trong nền và thử lại peer chưa kết nối sau `NETWORK_RETRY_INTERVAL = 5.0` giây; không bắt tay lại nếu đường kết nối đang hoạt động. Đóng ứng dụng dừng các vòng thử lại. Không cần và không còn tham số dòng lệnh `--join`.
- `JOIN`/`JOIN_ACK` là bắt tay nội bộ sau khi mở TCP để trao ID, địa chỉ và thành viên đã biết. Các địa chỉ được giới thiệu cũng có thể được dùng để mở thêm kết nối.
- `NetworkServer.peer_ids()` cung cấp ID đã phát hiện; `connect_configured_peer(node_config)` mở TCP theo cấu hình; `is_connected(peer_id)` kiểm tra đường gửi còn mở; `send()` tự kết nối lại tới địa chỉ thành viên đã biết khi cần gửi. Không suy ID từ IP. IP tự công bố phải là địa chỉ các máy khác truy cập được (IP ZeroTier khi chạy nhiều máy).
- Kết nối nhận được định danh bằng `from` cũng được dùng làm đường gửi ngược nếu chưa có đường gửi tới peer, hỗ trợ node tham gia sau khi coordinator đã chạy.

## 5. Giao thức
- Mỗi gói tin là một chuỗi JSON trên một dòng, kết thúc bằng `\n`.
- Mọi gói tin có trường `type` và `from` (ID node gửi).
- `from` là căn cứ định danh node gửi ở tầng mạng: không suy ID từ IP kết nối, kể cả để ghi log. Nhiều node chạy chung một máy có thể có cùng IP. Trường này chưa phải cơ chế xác thực người gửi.

## 6. Các loại gói tin
- `ELECTION`
- `ANSWER`
- `COORDINATOR`
- `HEARTBEAT`
- `HEARTBEAT_ACK`
- `JOIN`, `JOIN_ACK`: khai báo/gia nhập và giới thiệu thành viên.
- `REQUEST`
- `GRANT`
- `QUEUED`
- `RELEASE`
- `BALANCE_CHANGED`: nút vừa giao dịch thành công gửi trực tiếp tới các nút khác để cập nhật số dư giao diện.
- Ba loại thông điệp bầu chọn là `ELECTION`, `ANSWER`, `COORDINATOR`. `ANSWER` tương đương `OK` trong mô tả Bully thường gặp; code dùng tên `ANSWER`. `HEARTBEAT` và `HEARTBEAT_ACK` phục vụ theo dõi hoạt động.
- Coordinator trả `QUEUED` cho node vừa vào FIFO để phân biệt đang xếp hàng với không được phản hồi. `GRANT` và `QUEUED` đều dừng đồng hồ chờ phản hồi của đơn xin quyền.
- Tên thông điệp cấp quyền hiện dùng `REQUEST`, `GRANT`, `RELEASE`, `QUEUED` theo yêu cầu đổi tên. Bảng dưới ghi cấu trúc đang được code gửi; việc duyệt đầy đủ cấu trúc trường của nhóm vẫn nằm ở mục 9a.

| `type` | Cấu trúc gói tin | Ai gửi → ai |
|---|---|---|
| `JOIN` / `JOIN_ACK` | `{"type": "JOIN", "from": 2, "node": {"id": 2, "ip": "10.130.28.199", "port": 3002}, "peers": [{"id": 0, "ip": "10.130.28.217", "port": 3000}]}` | Node kết nối → thành viên đang chạy; bên nhận trả cùng cấu trúc với type `JOIN_ACK`, địa chỉ của mình và các thành viên đã biết |
| `ELECTION` | `{"type": "ELECTION", "from": 2}` | Node bắt đầu bầu cử → các node có ID lớn hơn |
| `ANSWER` | `{"type": "ANSWER", "from": 4, "to": 2}` | Chỉ node có ID lớn hơn node khởi động bầu cử → node khởi động |
| `COORDINATOR` | `{"type": "COORDINATOR", "from": 4}` | Node thắng cử broadcast cho tất cả; coordinator đang tại vị cũng trả riêng cho node ID nhỏ hơn khi nhận `ELECTION` (FLOWS mục 2) |
| `HEARTBEAT` | `{"type": "HEARTBEAT", "from": 4}` | Coordinator → từng peer, mỗi `HEARTBEAT_INTERVAL` |
| `HEARTBEAT_ACK` | `{"type": "HEARTBEAT_ACK", "from": 2, "to": 4}` | Peer (không phải coordinator) → coordinator |
| `REQUEST` | `{"type": "REQUEST", "from": 2}` | Node có giao dịch → coordinator |
| `GRANT` | `{"type": "GRANT", "from": 4}` | Coordinator → node được cấp quyền |
| `QUEUED` | `{"type": "QUEUED", "from": 4}` | Coordinator → node vừa được đưa vào hàng đợi FIFO |
| `RELEASE` | `{"type": "RELEASE", "from": 2}` | Node đang giữ quyền → coordinator |
| `BALANCE_CHANGED` | `{"type": "BALANCE_CHANGED", "from": 2}` | Nút vừa commit giao dịch thành công → các thành viên đã phát hiện |

### Đồng bộ số dư giao diện
- Chỉ gửi `BALANCE_CHANGED` sau khi giao dịch thành công và đã trả khoá; giao dịch bị từ chối hoặc phát sinh lỗi không gửi thông báo.
- Nút nhận đọc lại số dư từ DB trong luồng nền và đưa kết quả vào hàng đợi giao diện. Không phát lại thông báo, không thực hiện lại giao dịch, không dùng số dư do nút gửi cung cấp.
- Các lần đọc số dư giao diện dùng kết nối DB riêng và được xử lý tuần tự tại mỗi nút, tránh dùng chung phiên ghi giao dịch và tránh kết quả cũ ghi đè kết quả mới.
- Thông báo được gửi cố gắng tới từng nút; nếu gửi thất bại thì thử kết nối và gửi lại một lần. Không hoàn tác giao dịch đã commit khi nút khác offline. Chưa lưu thông báo để phát lại; nút mở lại ứng dụng sẽ đọc số dư hiện tại từ DB.
- Kết nối TCP tiếp tục chờ qua các lần timeout khi rảnh. Mỗi kết nối tuần tự hoá các lần gửi để heartbeat và thông báo số dư không xen lẫn byte.
- Nội dung log và nhãn giao diện sử dụng tiếng Việt có dấu; mã gói tin, mã sự kiện nội bộ và tên cột DB giữ nguyên.
- Đây là thông báo qua TCP của ứng dụng, không sử dụng Supabase Realtime hay Socket.IO. Các node cần chạy cùng bản mã nguồn để xử lý thông báo mới.

### Trạng thái cấp quyền tập trung

- `BankService._lock_holder`: `None` là rảnh, có ID là đang bận và chỉ rõ node giữ quyền; tương đương vai trò biến `busy`, không có một biến `busy` riêng.
- `BankService._lock_queue`: danh sách chờ FIFO, lấy phần tử đầu khi người giữ quyền gửi `RELEASE`.
- `_access_mutex` bảo vệ toàn bộ bước kiểm tra/cập nhật holder và FIFO trong `request_lock()`, `release_lock()`, `reset_lock()`. Khi trả quyền, việc lấy node kế tiếp và gán holder là một thao tác được bảo vệ; mutex được nhả trước khi ghi log, gửi mạng hoặc chạy giao dịch.
- Giao dịch lỗi vẫn đi qua `finally` để trả quyền. `RELEASE` từ node không giữ quyền bị bỏ qua. Nhận `GRANT` khi không còn giao dịch chờ thì trả quyền ngay.
- `TRANSACTION_DELAY = 3.0` trong `main.py` là độ trễ demo, đặt `0` để tắt. Runtime truyền callback `before_write` cho `deposit()`/`withdraw()`; callback chạy sau khi đọc số dư và kiểm tra đủ tiền, trước khi UPDATE/INSERT. Trong lúc chờ, node vẫn giữ quyền nhưng không giữ `_access_mutex`, nên coordinator vẫn tiếp nhận yêu cầu để xếp FIFO.
- Sự kiện `transaction_status` hiển thị chờ quyền, xếp hàng, đếm ngược theo thời gian còn lại và kết quả. `transaction_busy` vô hiệu hoá hai nút giao dịch trong lúc có đơn đang chờ/đang chạy và mở lại sau khi trả quyền hoặc huỷ đơn chờ. Đếm ngược chỉ bắt đầu khi được cấp quyền và đã đọc số dư; rút vượt số dư bị từ chối ngay.
- `_transaction_running` ngăn `GRANT` lặp tạo thêm worker khi giao dịch đang chạy. Đơn đang chạy không bị xoá như một đơn chỉ đang xếp hàng. Đóng node trong lúc đếm ngược làm callback báo huỷ và rollback trước khi ghi.
- Chưa có lease, chống yêu cầu trùng hoặc kiểm tra đầy đủ người gửi `GRANT`/`QUEUED`. Phần đổi tên thông điệp chưa bổ sung các cơ chế chịu lỗi này.

## 7. CSDL hiện tại
- Bảng `fund` (`id`, `balance bigint`, `updated_at`): có một dòng duy nhất.
- Bảng `transactions` (`id`, `node_id`, `type` DEPOSIT/WITHDRAW, `amount`, `created_at`).
- Kết nối qua Supabase Shared pooler chế độ session (cổng 5432) vì mạng nhà có thể chỉ có IPv4.
- Kiểm tra DB đang hoạt động và kết nối được trước buổi demo.
- Chuỗi kết nối lưu trong `.env`, không commit.
- `BankService(database_url=...)` ưu tiên chuỗi được truyền trực tiếp, sau đó tới biến môi trường `DATABASE_URL`, cuối cùng đọc `DATABASE_URL` trong `.env` ở gốc dự án. Không cần thư viện dotenv.

### Giao diện và log

- Tkinter/ttk, theme `clam`, dùng phông hệ thống; không thêm thư viện giao diện bên ngoài.
- Bố cục hai cột: nhật ký cuộn dọc bên trái; số dư, vai trò/điều phối viên, nhập số tiền và đếm ngược bên phải. Thời điểm cập nhật số dư là lúc giao diện nhận kết quả đọc DB, không phải thời gian commit giao dịch.
- Luồng nền chỉ gửi sự kiện qua `post_event()`; Tkinter xử lý hàng đợi ở luồng chính.
- Nhật ký giao diện có giờ và màu theo thông báo. Log file `logs/node_<id>.log` lưu UTF-8, log terminal dùng UTF-8 để tránh lỗi mã hoá tiếng Việt trên Windows.
- Khi nhận `JOIN` hoặc `JOIN_ACK` hợp lệ, cả hai phía ghi lên nhật ký giao diện thông báo kết nối kèm ID, IP và port của thành viên. Log này được tạo từ bắt tay thực tế, không chỉ từ việc có IP trong cấu hình.
- Thư mục `logs/` và file log được tự tạo khi có sự kiện; không cần giữ trong cây mã nguồn. Cache `__pycache__/` do Python tự tạo. Cả cache, log và môi trường `venv/` được bỏ qua trong Git.
- Hướng dẫn và giới hạn kiểm thử nằm trong [TESTING.md](TESTING.md).

## 8. ĐÃ CHỐT
- Python + Tkinter.
- ID 0 đến 5.
- Port tự gán theo ID.
- JSON phân tách bằng `\n`.
- Bully với `ELECTION` / `ANSWER` / `COORDINATOR` + heartbeat.
- ID lớn hơn thắng.
- Khởi động Bully ngay, đồng thời TCP tự kết nối theo cấu hình trong nền; không chờ các lần kết nối xong mới chạy Bully. Chỉ cần `--id`, không cần `--join`. `JOIN`/`JOIN_ACK` là thông điệp mạng nội bộ.
- Khoá tập trung tại coordinator (`REQUEST` / `GRANT` / `RELEASE`).
- Tên thông điệp cấp quyền đã bỏ hậu tố/tiền tố `LOCK`; phản hồi xếp hàng là `QUEUED`.
- Số dư trong DB Supabase.

## 9. CẦN CHỐT (sau khi các thành viên báo cáo)
- **a.** Duyệt đầy đủ cấu trúc trường JSON theo bảng code hiện tại ở mục 6. Tên thông điệp đã được đổi theo yêu cầu; trạng thái này không có nghĩa đã bổ sung xử lý yêu cầu trùng, mất thông điệp hoặc phân mảnh mạng.
- **b.** Tham số thời gian: CẦN CHỐT. Code hiện đang dùng (đơn vị giây):
  - `ELECTION_ANSWER_TIMEOUT = 5.0` (`core/bully.py`) — chờ `ANSWER` khi có thành viên ID cao hơn; không có thì tự làm coordinator ngay.
  - `HEARTBEAT_INTERVAL = 2.0` (`core/bully.py`) — chu kỳ coordinator gửi `HEARTBEAT` và chu kỳ vòng canh kiểm tra.
  - `HEARTBEAT_TIMEOUT = 3.0` (`core/bully.py`) — peer không trả `HEARTBEAT_ACK` quá ngần này bị coi là offline (chỉ để ghi log, không kích hoạt bầu cử).
  - `COORDINATOR_SILENCE_TIMEOUT = 6.0` (`core/bully.py`) — node phụ phát hiện coordinator im lặng, bầu lại (FLOWS mục 2a).
  - `REQUEST_REPLY_TIMEOUT = 10.0` (`main.py`) — chờ `GRANT`/`QUEUED` sau khi gửi `REQUEST`; quá hạn thì bầu lại (FLOWS mục 2b).
  - Đề xuất: chốt nguyên các giá trị trên nếu cả nhóm đồng ý; phương án khác: nới `COORDINATOR_SILENCE_TIMEOUT` ≥ 3 lần `HEARTBEAT_INTERVAL` để dung sai mạng ZeroTier.
- **c.** Xử lý khi node đang giữ quyền bị crash: CHƯA TRIỂN KHAI. Coordinator giữ nguyên `_lock_holder` cho tới khi có `RELEASE`; node đã nhận `QUEUED` có thể chờ vô hạn. Phương án cần thiết kế là lease kết hợp cơ chế ngăn người giữ quyền cũ tiếp tục ghi sau khi hết quyền. Chỉ cấp tiếp sau timeout không đủ bảo đảm an toàn nếu người giữ quyền cũ vẫn đang chạy.
- **d.** Xử lý khi đổi coordinator giữa chừng: CẦN CHỐT phần còn lại. Code hiện đã làm: coordinator cũ xoá holder + hàng đợi FIFO khi hạ vai trò (`BankService.reset_lock()`, log `ACCESS_RESET`), coordinator mới bắt đầu với hàng đợi trống; node đang chờ mà phát hiện đổi coordinator thì đơn xin bị huỷ và báo người dùng bấm lại (`main.py::NodeRuntime.update_status`). Còn bỏ ngỏ: code KHÔNG tự động gửi lại `REQUEST` như đề xuất cũ của mục này — cần chốt giữa "huỷ + báo bấm lại" (đang dùng) với "tự gửi lại" (đề xuất cũ).
- **e.** Có thêm kiểm tra phiên bản (version) khi ghi DB làm lớp bảo vệ phụ hay không.
- **f.** Driver DB (psycopg2-binary hay psycopg 3) và có tách file `core/db.py` hay không. (Ghi nhận: code hiện dùng `psycopg2`.)
- **g.** Đã bổ sung độ trễ mô phỏng 3 giây và đếm ngược cho nạp/rút theo yêu cầu. Công tắc "tắt khoá" và nút giả lập crash chưa triển khai.
