import json
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path


class Database:
    def __init__(self, db_path: Path, legacy_dir: Path):
        self.db_path = Path(db_path)
        self.legacy_dir = Path(legacy_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_legacy_json()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id TEXT PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                ip_name TEXT NOT NULL,
                shop_name TEXT NOT NULL,
                manager TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL UNIQUE,
                telegram_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                text TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                file_id TEXT,
                file_type TEXT,
                paid INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'В обработке',
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_orders_user
            ON orders(telegram_id);

            CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status);
            """)

    def _migrate_legacy_json(self):
        candidates = [self.legacy_dir, self.legacy_dir.parent]
        users_file = next((p / "users.json" for p in candidates if (p / "users.json").exists()), None)
        orders_file = next((p / "orders.json" for p in candidates if (p / "orders.json").exists()), None)
        counter_file = next((p / "order_counter.json" for p in candidates if (p / "order_counter.json").exists()), None)
        marker = self.db_path.parent / ".json_migrated"
        if marker.exists():
            return
        if not users_file and not orders_file:
            return

        if users_file.exists():
            try:
                users = json.loads(users_file.read_text(encoding="utf-8"))
                for uid, user in users.items():
                    self.upsert_user(uid, user)
            except Exception:
                pass

        if orders_file.exists():
            try:
                all_orders = json.loads(orders_file.read_text(encoding="utf-8"))
                with self.connect() as conn:
                    for uid, orders in all_orders.items():
                        for order in orders:
                            conn.execute(
                                """INSERT OR IGNORE INTO orders
                                (order_id, telegram_id, timestamp, text, comment, file_id, file_type, paid, status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    order.get("order_id"), uid,
                                    order.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M")),
                                    order.get("text", ""), order.get("comment", ""),
                                    order.get("file_id"), order.get("file_type"),
                                    int(bool(order.get("paid", False))),
                                    order.get("status", "В обработке"),
                                ),
                            )
            except Exception:
                pass

        marker.write_text(datetime.now().isoformat(), encoding="utf-8")

    def upsert_user(self, uid, user):
        with self.connect() as conn:
            conn.execute("""INSERT INTO users
                (telegram_id, first_name, last_name, ip_name, shop_name, manager)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                first_name=excluded.first_name, last_name=excluded.last_name,
                ip_name=excluded.ip_name, shop_name=excluded.shop_name,
                manager=excluded.manager""",
                (uid, user.get("first_name", ""), user.get("last_name", ""),
                 user.get("ip_name", ""), user.get("shop_name", ""), user.get("manager")))

    def get_user(self, uid):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (uid,)).fetchone()
            return dict(row) if row else None

    def delete_user(self, uid):
        with self.connect() as conn:
            conn.execute("DELETE FROM users WHERE telegram_id=?", (uid,))

    def get_users(self):
        with self.connect() as conn:
            return {r["telegram_id"]: dict(r) for r in conn.execute("SELECT * FROM users")}

    def get_orders(self, uid):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM orders WHERE telegram_id=? ORDER BY id", (uid,)
            )]

    def get_last_order(self, uid):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE telegram_id=? ORDER BY id DESC LIMIT 1", (uid,)
            ).fetchone()
            return dict(row) if row else None

    def create_order(self, uid, text="", comment="", file_id=None, file_type=None):
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(order_id), 0) + 1 FROM orders").fetchone()
            order_id = int(row[0])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn.execute("""INSERT INTO orders
                (order_id, telegram_id, timestamp, text, comment, file_id, file_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (order_id, uid, timestamp, text, comment, file_id, file_type))
            return {
                "order_id": order_id,
                "telegram_id": uid,
                "text": text,
                "comment": comment,
                "file_id": file_id,
                "file_type": file_type,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

    def get_order_by_id(self, order_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            return dict(row) if row else None

    def update_order(self, order_id, **fields):
        allowed = {"text", "comment", "file_id", "file_type", "paid", "status"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sql = ", ".join(f"{k}=?" for k in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE orders SET {sql} WHERE order_id=?", (*fields.values(), order_id))

    def delete_last_order(self, uid):
        order = self.get_last_order(uid)
        if order:
            with self.connect() as conn:
                conn.execute("DELETE FROM orders WHERE id=?", (order["id"],))
        return order

    def set_last_paid(self, uid):
        order = self.get_last_order(uid)
        if order:
            self.update_order(order["order_id"], paid=1)
        return order

    def backup(self, backup_dir=None):
        if backup_dir is None:
            backup_dir = self.db_path.parent / "backups"

        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_path = backup_dir / f"bot_db_{timestamp}.db"

        source = sqlite3.connect(self.db_path)
        destination = sqlite3.connect(backup_path)

        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        return backup_path

    def set_status(self, order_id, status):
        order = self.get_order_by_id(order_id)
        if order:
            self.update_order(order_id, status=status)
        return order
