CREATE TABLE orders (
    id      VARCHAR(64) PRIMARY KEY,
    status  VARCHAR(32) NOT NULL
);

INSERT INTO orders (id, status) VALUES ('seed-1', 'NEW');
