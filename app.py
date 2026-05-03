import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, g

app = Flask(__name__)
DATABASE = '/tmp/database.db'  # Vercel 可写临时目录

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db

def init_db():
    db = get_db()
    cursor = db.cursor()
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS user (
            user_id TEXT PRIMARY KEY,
            user_name TEXT NOT NULL,
            phone TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS item (
            item_id TEXT PRIMARY KEY,
            item_name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            status INTEGER DEFAULT 0 CHECK(status IN (0,1)),
            seller_id TEXT NOT NULL,
            FOREIGN KEY (seller_id) REFERENCES user(user_id)
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL UNIQUE,
            buyer_id TEXT NOT NULL,
            order_date TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES item(item_id),
            FOREIGN KEY (buyer_id) REFERENCES user(user_id)
        );
    ''')
    # 插入初始数据（若为空）
    if cursor.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0:
        cursor.executescript('''
            INSERT INTO user VALUES ('u001','ZhangSan','13800000001');
            INSERT INTO user VALUES ('u002','LiSi','13800000002');
            INSERT INTO user VALUES ('u003','WangWu','13800000003');
            INSERT INTO user VALUES ('u004','ZhaoLiu','13800000004');
            INSERT INTO item VALUES ('i001','CalculusBook','Book',200,0,'u001');
            INSERT INTO item VALUES ('i002','DeskLamp','DailyGoods',35,1,'u002');
            INSERT INTO item VALUES ('i003','Microcontroller','Electronics',800,0,'u001');
            INSERT INTO item VALUES ('i004','Chair','Furniture',50,1,'u003');
            INSERT INTO item VALUES ('i005','WaterBottle','DailyGoods',15,0,'u004');
            INSERT INTO orders (item_id,buyer_id,order_date) VALUES ('i002','u001','2025-01-15');
            INSERT INTO orders (item_id,buyer_id,order_date) VALUES ('i004','u002','2025-02-20');
        ''')
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ---------- 页面路由 ----------
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/items')
@app.route('/users')
@app.route('/orders')
def pages():
    return render_template('index.html')

# ---------- API 路由 ----------
@app.route('/api/stats')
def stats():
    db = get_db()
    total_items = db.execute("SELECT COUNT(*) FROM item").fetchone()[0]
    avg_price = db.execute("SELECT AVG(price) FROM item").fetchone()[0] or 0
    total_users = db.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    total_orders = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    category_counts = db.execute("SELECT category, COUNT(*) as count FROM item GROUP BY category").fetchall()
    top_seller = db.execute('''
        SELECT u.user_id, u.user_name, COUNT(i.item_id) as item_count
        FROM user u LEFT JOIN item i ON u.user_id = i.seller_id
        GROUP BY u.user_id ORDER BY item_count DESC LIMIT 1
    ''').fetchall()
    return jsonify(success=True, data={
        'total_items': total_items,
        'avg_price': round(avg_price, 2),
        'total_users': total_users,
        'total_orders': total_orders,
        'category_counts': [{'category': r['category'], 'count': r['count']} for r in category_counts],
        'top_seller': [{'user_id': r['user_id'], 'user_name': r['user_name'], 'item_count': r['item_count']} for r in top_seller]
    })

@app.route('/api/items')
def items():
    filter_type = request.args.get('filter', 'all')
    query = "SELECT * FROM item WHERE 1=1"
    params = []
    if filter_type == 'unsold':
        query += " AND status = 0"
    elif filter_type == 'price_gt_30':
        query += " AND price > 30"
    elif filter_type == 'dailygoods':
        query += " AND category = 'DailyGoods'"
    elif filter_type == 'seller_u001':
        query += " AND seller_id = 'u001'"
    items = get_db().execute(query, params).fetchall()
    return jsonify(success=True, data=[dict(r) for r in items])

@app.route('/api/items/add', methods=['POST'])
def add_item():
    data = request.get_json()
    try:
        db = get_db()
        db.execute("INSERT INTO item (item_id, item_name, category, price, status, seller_id) VALUES (?,?,?,?,0,?)",
                   (data['item_id'], data['item_name'], data['category'], data['price'], data['seller_id']))
        db.commit()
        return jsonify(success=True, message='商品添加成功')
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/items/update-price', methods=['POST'])
def update_price():
    data = request.get_json()
    try:
        db = get_db()
        db.execute("UPDATE item SET price = ? WHERE item_id = ?", (data['price'], data['item_id']))
        db.commit()
        return jsonify(success=True, message='价格修改成功')
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/items/delete', methods=['POST'])
def delete_item():
    data = request.get_json()
    item_id = data['item_id']
    db = get_db()
    item = db.execute("SELECT status FROM item WHERE item_id = ?", (item_id,)).fetchone()
    if not item:
        return jsonify(success=False, error='商品不存在')
    if item['status'] == 1:
        return jsonify(success=False, error='已售出商品不能删除')
    try:
        db.execute("DELETE FROM item WHERE item_id = ?", (item_id,))
        db.commit()
        return jsonify(success=True, message='商品已删除')
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/buy', methods=['POST'])
def buy_item():
    data = request.get_json()
    item_id = data['item_id']
    buyer_id = data['buyer_id']
    db = get_db()
    try:
        # 开始事务（显式）
        db.execute("BEGIN")
        item = db.execute("SELECT status FROM item WHERE item_id = ?", (item_id,)).fetchone()
        if not item:
            db.execute("ROLLBACK")
            return jsonify(success=False, error='商品不存在')
        if item['status'] == 1:
            db.execute("ROLLBACK")
            return jsonify(success=False, error='该商品已售出，无法重复购买')
        # 检查是否已有该商品订单（唯一约束）
        exist = db.execute("SELECT 1 FROM orders WHERE item_id = ?", (item_id,)).fetchone()
        if exist:
            db.execute("ROLLBACK")
            return jsonify(success=False, error='该商品已有订单记录')
        # 插入订单
        db.execute("INSERT INTO orders (item_id, buyer_id, order_date) VALUES (?,?,?)",
                   (item_id, buyer_id, datetime.now().strftime('%Y-%m-%d')))
        # 更新商品状态
        db.execute("UPDATE item SET status = 1 WHERE item_id = ?", (item_id,))
        db.execute("COMMIT")
        return jsonify(success=True, message='购买成功！')
    except Exception as e:
        db.execute("ROLLBACK")
        return jsonify(success=False, error=str(e))

@app.route('/api/users')
def users():
    users = get_db().execute('''
        SELECT u.*, COUNT(i.item_id) as item_count
        FROM user u LEFT JOIN item i ON u.user_id = i.seller_id
        GROUP BY u.user_id
    ''').fetchall()
    return jsonify(success=True, data=[dict(r) for r in users])

@app.route('/api/orders')
def orders():
    orders = get_db().execute("SELECT * FROM orders").fetchall()
    return jsonify(success=True, data=[dict(r) for r in orders])

@app.route('/api/orders/sold-with-buyer')
def sold_with_buyer():
    rows = get_db().execute('''
        SELECT i.item_name, u.user_name as buyer_name, o.order_date, i.price
        FROM orders o
        JOIN item i ON o.item_id = i.item_id
        JOIN user u ON o.buyer_id = u.user_id
    ''').fetchall()
    return jsonify(success=True, data=[dict(r) for r in rows])

@app.route('/api/orders/u001-items-status')
def u001_items_status():
    rows = get_db().execute('''
        SELECT i.item_id, i.item_name, i.price, i.status,
               CASE WHEN i.status = 1 THEN u.user_name ELSE NULL END as buyer_name,
               CASE WHEN i.status = 1 THEN 1 ELSE 0 END as is_sold
        FROM item i
        LEFT JOIN orders o ON i.item_id = o.item_id
        LEFT JOIN user u ON o.buyer_id = u.user_id
        WHERE i.seller_id = 'u001'
    ''').fetchall()
    return jsonify(success=True, data=[dict(r) for r in rows])

@app.route('/api/view/sold')
def view_sold():
    rows = get_db().execute('''
        SELECT i.item_name, o.buyer_id, o.order_date
        FROM item i JOIN orders o ON i.item_id = o.item_id
        WHERE i.status = 1
    ''').fetchall()
    return jsonify(success=True, data=[dict(r) for r in rows])

@app.route('/api/view/unsold')
def view_unsold():
    rows = get_db().execute("SELECT * FROM item WHERE status = 0").fetchall()
    return jsonify(success=True, data=[dict(r) for r in rows])

if __name__ == '__main__':
    app.run(debug=True)