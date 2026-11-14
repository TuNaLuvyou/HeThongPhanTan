# Hệ thống Quỹ chung Phân tán (Distributed Shared Fund)

Hệ thống Quỹ chung Phân tán áp dụng giải thuật Bully và cơ chế loại trừ tương hỗ tập trung viết bằng Python 3.

---

## 1. Mô tả bài toán
Quỹ dùng chung phân tán giữa 6 máy trạm, áp dụng giải thuật Bully (bầu chọn coordinator) và loại trừ tương hỗ tập trung (coordinator cấp quyền truy cập số dư lưu trong DB).
Trỏ tới thư mục `docs/` để xem chi tiết.

## 2. Cài đặt
- **Yêu cầu môi trường**: Python 3.9+
- **Khởi tạo và kích hoạt môi trường ảo (venv)**:
  - Tạo môi trường ảo:
    ```bash
    python -m venv venv
    ```
  - Kích hoạt môi trường ảo:
    - **Windows (PowerShell)**:
      ```powershell
      .\venv\Scripts\Activate.ps1
      ```
    - **Windows (Command Prompt)**:
      ```cmd
      .\venv\Scripts\activate.bat
      ```
    - **macOS / Linux**:
      ```bash
      source venv/bin/activate
      ```
- **Cài đặt thư viện**:
  ```bash
  pip install -r requirements.txt
  ```
- **Hỗ trợ Tkinter**:
  - Linux: `sudo apt install python3-tk`
  - macOS (dùng Python của Homebrew): `brew install python-tk`
  - Windows: Mặc định Tkinter đã đi kèm với bộ cài Python chính thức.
- **Biến môi trường**:
  - Sao chép `.env.example` thành `.env`
  - Điền `DATABASE_URL` thực tế lấy từ trưởng nhóm.
  - Ứng dụng tự đọc `.env` ở gốc dự án; biến môi trường `DATABASE_URL` của hệ điều hành được ưu tiên nếu đã đặt.
  - Mỗi máy tự tạo `.env`; không đưa thông tin kết nối thật lên Git.
- **Chuẩn bị Supabase**: chạy nội dung `db/schema.sql` trong SQL Editor nếu chưa tạo bảng `fund` và `transactions`. Tất cả các máy kết nối cùng một DB.

## 3. Cách chạy từng node
Chạy lệnh khởi động từng node với tham số ID tương ứng:
```bash
python main.py --id <node_id>
```
Trong đó `<node_id>` từ `0` đến `5`.
Không cần nhập port hay bấm mở server trên UI, hệ thống tự gán port theo ID từ `config.json`.

TCP tự kết nối các IP/port trong `config.json` ở nền ngay khi mở node. Bully chạy ngay, không chờ thử kết nối xong và không chờ đủ máy. Trên máy thứ hai chỉ cần chạy:

```bash
python main.py --id 2
```

Không cần tham số `--join`. Mỗi peer có một worker kết nối riêng; máy chưa mở hoặc vừa mất kết nối được thử lại sau 5 giây. Kết nối đang hoạt động được giữ lại, không gửi bắt tay lặp mỗi vòng. Khi nối TCP, các node tự trao ID và địa chỉ bằng `JOIN`/`JOIN_ACK`; đây là thông điệp nội bộ.

Khi dùng chung DB, cần chờ các node kết nối và thống nhất điều phối viên trước khi giao dịch. Nếu cấu hình chỉ có địa chỉ của chính mình hoặc các peer không truy cập được, node chưa thể biết các máy khác.

### Thử hai máy qua ZeroTier

1. Các máy tham gia cùng mạng ZeroTier và được duyệt thiết bị.
2. Trong `config.json`, điền **IP ZeroTier và port của các máy cần kết nối**, gồm máy đang chạy; dùng cùng danh sách địa chỉ trên các máy. Không điền Network ID hay Public IP. Cho phép Python nhận kết nối qua firewall.
3. Cấu hình hiện tại dành cho Windows node `0` (`10.130.28.217:3000`) và macOS node `2` (`10.130.28.199:3002`). Các node còn lại đang dùng localhost, cần đổi IP trước khi chạy thêm máy.
4. Windows chạy `python main.py --id 0`; macOS chạy `python3 main.py --id 2` (hoặc `python` nếu đã kích hoạt venv). Hai máy có thể mở theo thứ tự bất kỳ; TCP tự kết nối trong nền.
5. Khi cả hai cùng hoạt động và kết nối thông suốt, hai giao diện phải cùng hiển thị điều phối viên `2`.

Node bật Bully ngay và tự kết nối TCP theo cấu hình trong nền, không chờ đủ máy. Node có ID lớn nhất trong nhóm đã kết nối làm điều phối viên; node mở sau được nhận biết mà không cần khởi động lại các node cũ.

Giao diện dùng Tkinter/ttk với tiếng Việt có dấu: số dư quỹ, vai trò hiện tại, nhập tiền nạp/rút và nhật ký có thời gian. Sau giao dịch thành công, các node khác nhận thông báo TCP và đọc lại số dư từ DB. Thông báo này không được lưu để phát lại khi máy mất kết nối.

Nạp/rút mặc định chờ mô phỏng **3 giây sau khi đọc số dư, trước khi ghi DB**, hiển thị đếm ngược trên giao diện để quan sát người giữ quyền và FIFO. Node đang xếp hàng chỉ hiện chờ quyền; khi được cấp mới bắt đầu đếm. Hai nút giao dịch của node đang chờ/đang chạy được vô hiệu hoá; giao diện và các node khác vẫn hoạt động. Đổi `TRANSACTION_DELAY` trong `main.py` nếu cần thời gian khác, hoặc đặt `0` để tắt. Mutex nội bộ chỉ bảo vệ trạng thái cấp quyền, không giữ trong thời gian mô phỏng.

### Kiểm thử

```bash
python -m unittest discover -s tests
```

Trên macOS ngoài venv, dùng `python3`. Bộ test hiện có **57 test**, không yêu cầu DB thật hay mở cửa sổ, gồm tự kết nối TCP theo cấu hình, node mở sau, nhiều luồng xin/trả quyền và đếm ngược trước khi ghi DB. Xem [kịch bản kiểm thử](docs/TESTING.md) để thử giao diện, DB và ZeroTier; xem [giao thức](docs/TECHS.md) và [luồng hoạt động](docs/FLOWS.md) để hiểu cách xử lý.

Để chỉ xem giao diện, không kết nối DB hay mạng:

```bash
python -m ui.app_window
```

Chế độ xem riêng giao diện chỉ ghi nhận thao tác mẫu, không thực hiện giao dịch thật. Hệ thống chưa thu hồi khoá khi node giữ quyền bị crash; cần tránh tình huống này trong demo thông thường hoặc kiểm thử riêng theo tài liệu.

### Cấu trúc thư mục

```text
core/             TCP và thành viên mạng, Bully, cấp quyền FIFO và truy cập DB
ui/               Giao diện Tkinter
db/schema.sql     Lược đồ và dữ liệu khởi tạo
tests/            Kiểm thử tự động
docs/             Nghiệp vụ, giao thức, luồng và hướng dẫn kiểm thử
main.py           Khởi động và ghép các module
config.json       Cấu hình node
requirements.txt  Thư viện cần cài
.env.example      Mẫu cấu hình kết nối DB
```

`venv/` là môi trường Python cục bộ. `logs/` và `__pycache__/` tự tạo lại khi chạy, được bỏ qua trong Git và có thể dọn khi không cần dữ liệu cũ. Script tạo PR, nội dung PR cũ và script smoke UI tạm trong `logs/` đã được dọn; các lệnh chạy ứng dụng và unittest không phụ thuộc các file này.

## 4. Phân công thành viên

| STT | Vai trò | Hệ điều hành | Họ tên | File phụ trách | Nhiệm vụ |
|:---:|---|---|---|---|---|
| 1 | Trưởng nhóm | macOS ARM | (điền tên) | main.py, docs/, repo Git | Quản lý repo Git, thiết kế gói tin JSON (ghi vào docs/TECHS.md), lập trình main.py, tích hợp hệ thống. |
| 2 | Thành viên 2 | macOS ARM | (điền tên) | core/bully.py | Lập trình giải thuật Bully (ELECTION, ANSWER, COORDINATOR, heartbeat, phát hiện coordinator lỗi). |
| 3 | Thành viên 3 | Linux 1 | (điền tên) | core/network.py | Lập trình tầng mạng Socket TCP (bind port theo ID, gửi/nhận JSON). |
| 4 | Thành viên 4 | Linux 2 | (điền tên) | core/bank_service.py, db/schema.sql, config.json | Lập trình Quỹ chung, truy cập DB và cơ chế khoá loại trừ tương hỗ; quản lý IP/cấu hình mạng (ZeroTier). |
| 5 | Thành viên 5 | Windows 1 | (điền tên) | ui/app_window.py | Lập trình giao diện Desktop Tkinter. |
| 6 | Thành viên 6 | Windows 2 | (điền tên) | (kịch bản test, log, báo cáo) | Xây dựng kịch bản kiểm thử demo, thu thập log, viết báo cáo đồ án. |

## 5. Quy trình làm việc
1. Mỗi người code phần của mình theo `docs/`.
2. Báo cáo lại theo mẫu trong `docs/AGENTS.md`.
3. Cả nhóm họp chốt các mục **CẦN CHỐT** trong `docs/TECHS.md`.
4. Trưởng nhóm tích hợp.
