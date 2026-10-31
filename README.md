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
    python -m venv .venv
    ```
  - Kích hoạt môi trường ảo:
    - **Windows (PowerShell)**:
      ```powershell
      .\.venv\Scripts\Activate.ps1
      ```
    - **Windows (Command Prompt)**:
      ```cmd
      .\.venv\Scripts\activate.bat
      ```
    - **macOS / Linux**:
      ```bash
      source .venv/bin/activate
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

## 3. Cách chạy từng node
Chạy lệnh khởi động từng node với tham số ID tương ứng:
```bash
python main.py --id <node_id>
```
Trong đó `<node_id>` từ `0` đến `5`.
Không cần nhập port hay bấm mở server trên UI, hệ thống tự gán port theo ID từ `config.json`.

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
