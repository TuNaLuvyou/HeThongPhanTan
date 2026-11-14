-- db/schema.sql - Thanh vien 4 - Tao bang fund va transactions theo docs/TECHS.md muc 7

-- Bang gom: gom cac giao dich cua toan he thong (co mot dong duy nhat).
CREATE TABLE IF NOT EXISTS fund (
    id BIGSERIAL PRIMARY KEY,
    balance BIGINT NOT NULL DEFAULT 10000000,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tao san ban duy nhat (so du ban dau).
INSERT INTO fund (id, balance, updated_at) VALUES (1, 10000000, now()) ON CONFLICT (id) DO NOTHING;

-- Bang ghi vet giao dich: node, loai (DEPOSIT/WITHDRAW), so tien, thoi gian.
CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    node_id INT NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('DEPOSIT', 'WITHDRAW')),
    amount BIGINT NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
