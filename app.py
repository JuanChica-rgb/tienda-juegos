import os
import sqlite3
from flask import Flask, render_template, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')
DB_NAME = 'tienda_juegos.db'

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                es_admin INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS juegos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                descripcion TEXT NOT NULL,
                plataforma TEXT NOT NULL, -- 'PC', 'APK' o 'Ambos'
                imagen_url TEXT,
                creado_en DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS juego_tags (
                juego_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (juego_id, tag_id),
                FOREIGN KEY (juego_id) REFERENCES juegos(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS enlaces_descarga (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                juego_id INTEGER NOT NULL,
                servidor TEXT NOT NULL, -- Ej: 'Mega', 'Google Drive', 'MediaFire'
                url TEXT NOT NULL,
                FOREIGN KEY (juego_id) REFERENCES juegos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS comentarios_valoraciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                juego_id INTEGER NOT NULL,
                usuario_nombre TEXT DEFAULT 'Anónimo',
                puntuacion INTEGER CHECK(puntuacion BETWEEN 1 AND 5),
                comentario TEXT NOT NULL,
                creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (juego_id) REFERENCES juegos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
        ''')
        # Crear un admin por defecto si no existe ninguno
        admin = conn.execute('SELECT * FROM usuarios WHERE es_admin = 1').fetchone()
        if not admin:
            pwd = generate_password_hash('admin123')
            conn.execute('INSERT INTO usuarios (nombre, email, password_hash, es_admin) VALUES (?, ?, ?, 1)',
                         ('Administrador', 'admin@juegos.com', pwd))
        
        # Logo o título por defecto
        conn.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("logo_url", "")')
        conn.execute('INSERT OR IGNORE INTO configuracion (clave, valor) VALUES ("titulo_web", "Portal de Juegos & APKs")')

init_db()

@app.route('/')
def home():
    return render_template('index.html')

# --- RUTAS DE ADMINISTRACIÓN ---
@app.route('/api/login-admin', methods=['POST'])
def login_admin():
    data = request.get_json()
    user = conn_execute('SELECT * FROM usuarios WHERE email = ? AND es_admin = 1', (data.get('email'),))
    # Autenticación simplificada
    with get_db() as conn:
        user = conn.execute('SELECT * FROM usuarios WHERE email = ? AND es_admin = 1', (data.get('email'),)).fetchone()
        if user and check_password_hash(user['password_hash'], data.get('password')):
            session['admin_id'] = user['id']
            session['admin_name'] = user['nombre']
            return jsonify({'status': 'ok', 'mensaje': 'Bienvenido Admin'})
    return jsonify({'status': 'error', 'mensaje': 'Credenciales de admin inválidas'}), 401

@app.route('/api/admin/logout', methods=['POST'])
def logout_admin():
    session.pop('admin_id', None)
    session.pop('admin_name', None)
    return jsonify({'status': 'ok'})

@app.route('/api/admin/configuracion', methods=['POST'])
def cambiar_config():
    if 'admin_id' not in session:
        return jsonify({'status': 'error', 'mensaje': 'Acceso denegado'}), 403
    data = request.get_json()
    with get_db() as conn:
        if 'logo_url' in data:
            conn.execute('UPDATE configuracion SET valor = ? WHERE clave = "logo_url"', (data['logo_url'],))
        if 'titulo_web' in data:
            conn.execute('UPDATE configuracion SET valor = ? WHERE clave = "titulo_web"', (data['titulo_web'],))
    return jsonify({'status': 'ok'})

@app.route('/api/admin/juegos', methods=['POST'])
def agregar_juego():
    if 'admin_id' not in session:
        return jsonify({'status': 'error', 'mensaje': 'Acceso denegado'}), 403
    
    data = request.get_json()
    titulo = data.get('titulo')
    descripcion = data.get('descripcion')
    plataforma = data.get('plataforma') # PC, APK, Ambos
    imagen_url = data.get('imagen_url')
    tags = data.get('tags', []) # Lista de strings: ["3D", "Anime"]
    enlaces = data.get('enlaces', []) # Lista de dicts: [{"servidor": "Mega", "url": "..."}]

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO juegos (titulo, descripcion, plataforma, imagen_url) VALUES (?, ?, ?, ?)',
            (titulo, descripcion, plataforma, imagen_url)
        )
        juego_id = cursor.lastrowid

        # Guardar / Vincular Tags
        for tag_nombre in tags:
            tag_nombre_clean = tag_nombre.strip().capitalize()
            if not tag_nombre_clean:
                continue
            conn.execute('INSERT OR IGNORE INTO tags (nombre) VALUES (?)', (tag_nombre_clean,))
            tag = conn.execute('SELECT id FROM tags WHERE nombre = ?', (tag_nombre_clean,)).fetchone()
            conn.execute('INSERT INTO juego_tags (juego_id, tag_id) VALUES (?, ?)', (juego_id, tag['id']))

        # Guardar Enlaces de Descarga
        for enlace in enlaces:
            if enlace.get('servidor') and enlace.get('url'):
                conn.execute(
                    'INSERT INTO enlaces_descarga (juego_id, servidor, url) VALUES (?, ?, ?)',
                    (juego_id, enlace['servidor'], enlace['url'])
                )

    return jsonify({'status': 'ok', 'mensaje': 'Juego publicado exitosamente'})

# --- RUTAS PÚBLICAS ---
@app.route('/api/juegos', methods=['GET'])
def obtener_juegos():
    tag_filtro = request.args.get('tag')
    busqueda = request.args.get('q')

    with get_db() as conn:
        query = '''
            SELECT DISTINCT j.*, 
                   COALESCE(AVG(cv.puntuacion), 0) as promedio_estrellas,
                   COUNT(cv.id) as total_opiniones
            FROM juegos j
            LEFT JOIN juego_tags jt ON j.id = jt.juego_id
            LEFT JOIN tags t ON jt.tag_id = t.id
            LEFT JOIN comentarios_valoraciones cv ON j.id = cv.juego_id
            WHERE 1=1
        '''
        params = []

        if tag_filtro:
            query += ' AND t.nombre = ?'
            params.append(tag_filtro)

        if busqueda:
            query += ' AND (j.titulo LIKE ? OR j.descripcion LIKE ?)'
            params.append(f'%{busqueda}%')
            params.append(f'%{busqueda}%')

        query += ' GROUP BY j.id ORDER BY j.creado_en DESC'
        
        juegos_db = conn.execute(query, params).fetchall()
        
        resultado = []
        for j in juegos_db:
            # Traer tags del juego
            tags_db = conn.execute('''
                SELECT t.nombre FROM tags t 
                JOIN juego_tags jt ON t.id = jt.tag_id 
                WHERE jt.juego_id = ?
            ''', (j['id'],)).fetchall()
            
            # Traer enlaces de descarga
            enlaces_db = conn.execute('SELECT servidor, url FROM enlaces_descarga WHERE juego_id = ?', (j['id'],)).fetchall()
            
            # Traer comentarios
            comentarios_db = conn.execute('SELECT usuario_nombre, puntuacion, comentario, creado_en FROM comentarios_valoraciones WHERE juego_id = ? ORDER BY creado_en DESC', (j['id'],)).fetchall()

            j_dict = dict(j)
            j_dict['tags'] = [t['nombre'] for t in tags_db]
            j_dict['enlaces'] = [dict(e) for e in enlaces_db]
            j_dict['comentarios'] = [dict(c) for c in comentarios_db]
            resultado.append(j_dict)

        # Configuración global
        configs = conn.execute('SELECT * FROM configuracion').fetchall()
        config_dict = {c['clave']: c['valor'] for c in configs}
        config_dict['es_admin'] = ('admin_id' in session)

    return jsonify({'juegos': resultado, 'config': config_dict})

@app.route('/api/tags', methods=['GET'])
def obtener_tags():
    with get_db() as conn:
        tags = conn.execute('SELECT nombre FROM tags ORDER BY nombre ASC').fetchall()
    return jsonify({'tags': [t['nombre'] for t in tags]})

@app.route('/api/comentar', methods=['POST'])
def comentar():
    data = request.get_json()
    juego_id = data.get('juego_id')
    nombre = data.get('nombre', 'Anónimo')
    puntuacion = data.get('puntuacion', 5)
    comentario = data.get('comentario')

    with get_db() as conn:
        conn.execute(
            'INSERT INTO comentarios_valoraciones (juego_id, usuario_nombre, puntuacion, comentario) VALUES (?, ?, ?, ?)',
            (juego_id, nombre, puntuacion, comentario)
        )
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)