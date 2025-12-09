CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    whatsapp TEXT,
    ws_id TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO users (name, email, whatsapp, ws_id)
VALUES
    ('Alice Example', 'alice@example.com', '+15550101', 'alice-ws'),
    ('Bob Example', NULL, '+15550202', 'bob-ws'),
    ('Charlie Web', 'charlie@example.com', NULL, 'charlie-ws')
ON CONFLICT DO NOTHING;
