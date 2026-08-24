import os
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from datetime import datetime, timedelta
import mysql.connector
import random
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import qrcode

app = Flask(__name__)
app.secret_key = 'sistema_cia' 

# --- CONFIGURACIÓN DEL SERVIDOR DE CORREO (SMTP) ---
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = 'sistemacia.utc@gmail.com' 
app.config['MAIL_PASSWORD'] = 'jgryboucichqzaxo' 
app.config['MAIL_DEFAULT_SENDER'] = 'sistemacia.utc@gmail.com'
app.config['MAIL_USE_TIMEOUT'] = True
app.config['MAIL_TIMEOUT'] = 5
mail = Mail(app)

# Configuración para archivos de justificación
app.config['UPLOAD_FOLDER'] = 'static/uploads/justificaciones'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Estructuras volátiles en RAM para control de códigos OTP
registro_temporal = {}
recuperacion_temporal = {}
intentos_falidos = {}

# Conexion a la base de datos
def conectar_db():
    return mysql.connector.connect(
        host="MechUTC.mysql.pythonanywhere-services.com", user="MechUTC", password="Mecatronica_UTC", database="MechUTC$sistema_cia"
    )

# Asignación de roles en la ruta raíz
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    rol = session.get('rol')
    if rol == 'programador': 
        return redirect(url_for('panel_programador'))
    elif rol == 'alumno': 
        return redirect(url_for('dashboard_alumno'))
    elif rol == 'profesor': 
        return redirect(url_for('dashboard_profesor'))
    elif rol in ['directivo', 'administrativo']: 
        return redirect(url_for('dashboard_directivo'))
        
    return redirect(url_for('logout'))

@app.route('/registro')
def mostrar_formulario_registro():
    return render_template('registro.html')

@app.route('/permiso')
def mostrar_creador_pases():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('permiso.html')

@app.route('/historial')
def ver_historial():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
        
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT p.id, p.matricula, u.nombre, p.motivo, p.token_qr AS codigo_qr, 
               p.fecha_creacion AS hora_inicio, NULL AS hora_fin, p.estatus AS estado
        FROM permisos_salida p
        LEFT JOIN usuarios_sistema u ON p.matricula = u.matricula_clave
        ORDER BY p.id DESC LIMIT 50
    """)
    lista_permisos = cursor.fetchall()
    
    try:
        cursor.execute("""
            SELECT a.id, a.matricula, u.nombre, u.seccion, a.salon, a.fecha_hora, a.notas
            FROM asistencias_nfc a
            LEFT JOIN usuarios_sistema u ON a.matricula = u.matricula_clave
            ORDER BY a.fecha_hora DESC LIMIT 50
        """)
        lista_accesos = cursor.fetchall()
    except:
        lista_accesos = []
        
    db.close()
    return render_template('historial.html', lista_accesos=lista_accesos, lista_permisos=lista_permisos)

# --- AUTENTICACIÓN ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        identificador = request.form.get('usuario').strip()
        contrasena = request.form.get('contrasena')
        
        db = conectar_db()
        cursor = db.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM usuarios_sistema WHERE matricula_clave = %s", (identificador,))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("SELECT * FROM usuarios_admin WHERE usuario = %s", (identificador,))
            user = cursor.fetchone()
            if user:
                user['matricula_clave'] = user['usuario']
                user['rol'] = 'directivo'
                
        db.close()
        
        if user and (check_password_hash(user['contrasena'], contrasena) or user['contrasena'] == contrasena):
            if user['rol'] in ['profesor', 'directivo', 'administrativo'] and user.get('primer_ingreso', 0) == 1:
                flash('Por seguridad, al ser tu primer ingreso debes recuperar/asignar tu contraseña.', 'warning')
                return redirect(url_for('recuperar_password'))

            session['logged_in'] = True
            session['user_id'] = user.get('id_usuario', user.get('id'))
            session['nombre'] = user['nombre']
            session['matricula'] = user['matricula_clave']
            session['rol'] = user['rol']
            session['seccion'] = user.get('seccion', 'N/A')
            session['correo'] = user.get('correo', '')
                
            return redirect(url_for('index'))
        else:
            flash('Matrícula/Usuario o contraseña incorrectos.', 'danger')
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- AUTO-REGISTRO Y OTP ---
@app.route('/alumnos/solicitar_registro', methods=['POST'])
def solicitar_registro():
    correo = request.form.get('correo').strip()
    nombre = request.form.get('nombre')
    matricula = request.form.get('matricula').strip()
    seccion = request.form.get('seccion').strip() 
    
    if not correo.endswith('@alumno.utc.edu.mx'):
        flash('Registro denegado. Se requiere un correo con el dominio institucional @alumno.utc.edu.mx', 'danger')
        return redirect(url_for('mostrar_formulario_registro'))
        
    codigo_otp = str(random.randint(1000, 9999))
    
    try:
        msg = Message("Código de Activación C.I.A. - UTC", recipients=[correo])
        msg.body = f"Hola {nombre},\n\nTu token de validación es: {codigo_otp} \n\nTienes 5 minutos para utilizarlo."
        mail.send(msg)
        
        registro_temporal[correo] = {
            "codigo": codigo_otp,
            "token_validado": False,
            "datos": {"matricula": matricula, "nombre": nombre, "correo": correo, "seccion": seccion}
        }
        session['correo_verificando'] = correo
        return redirect(url_for('pantalla_verificar_codigo'))
    except Exception as e:
        flash('Ocurrió un error al despachar el correo con el código de verificación.', 'danger')
        return redirect(url_for('mostrar_formulario_registro'))

@app.route('/verificar_codigo', methods=['GET', 'POST'])
def pantalla_verificar_codigo():
    correo = session.get('correo_verificando')
    if not correo or correo not in registro_temporal:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        codigo_ingresado = request.form.get('codigo_otp')
        if codigo_ingresado == registro_temporal[correo]['codigo']:
            registro_temporal[correo]['token_validado'] = True
            return redirect(url_for('pantalla_asignar_password'))
        else:
            flash('El código OTP ingresado es incorrecto.', 'danger')
            
    return render_template('verificar_codigo.html', correo=correo)

@app.route('/reenviar_codigo', methods=['POST'])
def reenviar_codigo():
    correo = session.get('correo_verificando')
    if not correo or correo not in registro_temporal:
        return redirect(url_for('login'))
        
    nuevo_codigo = str(random.randint(1000, 9999))
    registro_temporal[correo]['codigo'] = nuevo_codigo
    
    try:
        msg = Message("Nuevo Código de Activación C.I.A. - UTC", recipients=[correo])
        msg.body = f"Hola, tu nuevo token de validación es: {nuevo_codigo}"
        mail.send(msg)
        flash('Se ha reenviado un nuevo código a tu correo institucional.', 'info')
    except Exception as e:
        flash('No se pudo reenviar el correo. Intenta de nuevo más tarde.', 'danger')
        
    return redirect(url_for('pantalla_verificar_codigo'))

@app.route('/asignar_password', methods=['GET', 'POST'])
def pantalla_asignar_password():
    correo = session.get('correo_verificando')
    if not correo or correo not in registro_temporal or not registro_temporal[correo]['token_validado']:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        nueva_contrasena = request.form.get('contrasena')
        datos = registro_temporal[correo]['datos']
        password_cifrado = generate_password_hash(nueva_contrasena)
        
        db = conectar_db()
        cursor = db.cursor()
        try:
            cursor.execute("""
                INSERT INTO usuarios_sistema (matricula_clave, nombre, correo, contrasena, rol, seccion) 
                VALUES (%s, %s, %s, %s, 'alumno', %s)
            """, (datos['matricula'], datos['nombre'], datos['correo'], password_cifrado, datos['seccion']))
            db.commit()
            
            del registro_temporal[correo]
            session.pop('correo_verificando', None)
            flash('Cuenta activada correctamente. Ya puedes ingresar.', 'success')
            return redirect(url_for('login'))
        except mysql.connector.Error:
            flash('Error de consistencia: La matrícula o el correo ya existen.', 'danger')
            return redirect(url_for('login'))
        finally:
            db.close()
            
    return render_template('asignar_password.html', correo=correo)

@app.route('/recuperar_password', methods=['GET', 'POST'])
def recuperar_password():
    if request.method == 'POST':
        correo_o_user = request.form.get('identificador').strip()
        db = conectar_db()
        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM usuarios_sistema WHERE correo = %s OR matricula_clave = %s", (correo_o_user, correo_o_user))
        usuario = cursor.fetchone()
        tabla_origen = "usuarios_sistema"
        if not usuario:
            cursor.execute("SELECT * FROM usuarios_admin WHERE usuario = %s", (correo_o_user,))
            usuario = cursor.fetchone()
            tabla_origen = "usuarios_admin"
        db.close()
        
        if usuario:
            destino_correo = usuario.get('correo')
            codigo_reset = str(random.randint(1000, 9999))
            try:
                msg = Message("Restablecer Contraseña - Sistema C.I.A.", recipients=[destino_correo])
                msg.body = f"Hola,\n\nTu código de reinicio es: {codigo_reset}"
                mail.send(msg)
                
                recuperacion_temporal[destino_correo] = {"codigo": codigo_reset, "user_id": usuario.get('id_usuario', usuario.get('id')), "tabla": tabla_origen}
                session['correo_reseteando'] = destino_correo
                return redirect(url_for('confirmar_reset'))
            except Exception:
                flash('Falla del servidor SMTP al despachar el e-mail.', 'danger')
        else:
            flash('No se encontró ninguna cuenta vinculada.', 'danger')
    return render_template('recuperar_password.html')

@app.route('/confirmar_reset', methods=['GET', 'POST'])
def confirmar_reset():
    correo = session.get('correo_reseteando')
    if not correo or correo not in recuperacion_temporal: 
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        codigo = request.form.get('codigo_otp')
        password_nuevo = request.form.get('nueva_contrasena')
        datos_reset = recuperacion_temporal[correo]
        
        if codigo == datos_reset['codigo']:
            password_cifrado = generate_password_hash(password_nuevo)
            token_qr = f"ACCESO-{datos_reset['user_id']}-{random.randint(1000,9999)}"
            img = qrcode.make(token_qr)
            img.save(f"static/qrs/{token_qr}.png")
            
            db = conectar_db()
            cursor = db.cursor()
            id_columna = "id_usuario" if datos_reset['tabla'] == "usuarios_sistema" else "id"
            cursor.execute(f"UPDATE {datos_reset['tabla']} SET contrasena = %s, primer_ingreso = 0, qr_acceso = %s WHERE {id_columna} = %s", (password_cifrado, token_qr, datos_reset['user_id']))
            db.commit()
            db.close()
            
            del recuperacion_temporal[correo]
            flash('Su contraseña ha sido restablecida y su QR de acceso generado.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Código OTP incorrecto.', 'danger')
    return render_template('confirmar_reset.html', correo=correo)

# --- DASHBOARD DEL ALUMNO ---
@app.route('/alumno/dashboard')
def dashboard_alumno():
    if not session.get('logged_in') or session.get('rol') != 'alumno': 
        return redirect(url_for('login'))
    
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT h.*, u.nombre as profesor, u.correo as correo_profesor
        FROM horarios_clases h
        LEFT JOIN usuarios_sistema u ON h.clave_profesor = u.matricula_clave
        WHERE h.seccion = %s
    """, (session.get('seccion'),))
    horarios = cursor.fetchall()
    
    matricula_actual = session.get('matricula')
    conteo_asistencias = 0
    conteo_retardos = 0
    conteo_faltas = 0
    
    try:
        cursor.execute("""
            SELECT notas, COUNT(*) as total 
            FROM asistencias_nfc 
            WHERE matricula = %s 
            GROUP BY notas
        """, (matricula_actual,))
        resultados_asistencia = cursor.fetchall()
        
        for row in resultados_asistencia:
            estado = str(row['notas']).lower()
            if 'asistencia' in estado or estado == '':
                conteo_asistencias += row['total']
            elif 'retardo' in estado:
                conteo_retardos += row['total']
            elif 'falta' in estado:
                conteo_faltas += row['total']
    except Exception as e:
        print(f"Nota sobre métricas NFC: {e}")

    db.close()
    
    return render_template(
        'panel_alumno.html', 
        horario_alumno=horarios,
        conteo_asistencias=conteo_asistencias,
        conteo_retardos=conteo_retardos,
        conteo_faltas=conteo_faltas
    )

@app.route('/alumno/justificar', methods=['POST'])
def solicitar_justificacion():
    fecha_falta = request.form.get('fecha_falta')
    profesor_correo = request.form.get('profesor')
    motivo = request.form.get('motivo')
    archivo = request.files.get('documento')
    
    fecha_obj = datetime.strptime(fecha_falta, '%Y-%m-%d')
    if (datetime.now() - fecha_obj).days > 3:
        flash('Solo puedes justificar faltas de los últimos 3 días.', 'danger')
        return redirect(url_for('dashboard_alumno'))

    filename = ""
    if archivo:
        filename = secure_filename(archivo.filename)
        archivo.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
    db = conectar_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO justificaciones (matricula, fecha_falta, motivo, documento, estado) VALUES (%s, %s, %s, %s, 'Pendiente')", 
                   (session['matricula'], fecha_falta, motivo, filename))
    db.commit()
    db.close()
    
    destinatarios = ['coordinador@utc.edu.mx']
    if profesor_correo != 'TODOS': 
        destinatarios.append(profesor_correo)
    
    msg = Message("Solicitud de Justificación de Inasistencia - CIA UTC", recipients=destinatarios)
    msg.body = f"""Estimado Profesor / Coordinador,

Por medio de la presente, el alumno {session['nombre']} con matrícula {session['matricula']} del grupo {session['seccion']}, solicita la justificación de su inasistencia correspondiente al día {fecha_falta}.

Motivo expuesto: {motivo}

El documento probatorio ha sido cargado en el sistema CIA para su revisión.
Agradeciendo de antemano su atención y respuesta.

Atentamente,
Sistema C.I.A. (Control Institucional de Asistencia)
"""
    mail.send(msg)
    flash('Justificación enviada correctamente a los profesores y coordinación.', 'success')
    return redirect(url_for('dashboard_alumno'))

@app.route('/alumno/adelantar', methods=['POST'])
def solicitar_adelanto():
    clase_reemplazo = request.form.get('clase_reemplazo')
    profesor_adelanto = request.form.get('profesor_adelanto')
    horario_propuesto = request.form.get('horario_propuesto')
    motivo = request.form.get('motivo')
    
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("""
        INSERT INTO adelantos_clase (matricula, clase_reemplazada, profesor_solicitado, horario_propuesto, motivo, estado_admin, estado_profesor)
        VALUES (%s, %s, %s, %s, %s, 'Pendiente', 'Pendiente')
    """, (session['matricula'], clase_reemplazo, profesor_adelanto, horario_propuesto, motivo))
    id_solicitud = cursor.lastrowid
    db.commit()
    
    cursor.execute("SELECT correo FROM usuarios_sistema WHERE matricula_clave = %s", (profesor_adelanto,))
    profe_db = cursor.fetchone()
    db.close()

    link_admin = url_for('admin_validar_adelanto', id=id_solicitud, _external=True)
    msg_admin = Message("Solicitud de Adelanto de Clase (Aprobación Requerida)", recipients=['coordinador@utc.edu.mx'])
    msg_admin.body = f"El alumno {session['nombre']} ({session['seccion']}) solicita adelantar clase.\nHorario: {horario_propuesto}\nMotivo: {motivo}\n\nPara ACEPTAR o DECLINAR, ingrese aquí: {link_admin}"
    mail.send(msg_admin)
    
    link_profe = url_for('profe_validar_adelanto', id=id_solicitud, _external=True)
    msg_profe = Message("Propuesta de Adelanto de Clase por Alumnos", recipients=[profe_db['correo']])
    msg_profe.body = f"El grupo {session['seccion']} solicita adelantar su clase al horario {horario_propuesto}.\nPara dar su veredicto, revise este enlace: {link_profe}"
    mail.send(msg_profe)
    
    flash('Petición de adelanto enviada a coordinación y al profesor.', 'success')
    return redirect(url_for('dashboard_alumno'))

@app.route('/generar_pase', methods=['POST'])
def generar_pase_salida():
    matricula = request.form.get('matricula', session.get('matricula'))
    motivo = request.form.get('motivo')
    salon_orig = request.form.get('salon_origen', 'Aula Base')
    salon_dest = request.form.get('salon_destino', 'Destino Escolar')
    
    token = f"SALIDA-{matricula}-{random.randint(100000, 999999)}"
    
    try:
        img = qrcode.make(token)
        img.save(f"static/qrs/{token}.png")
        qr_url = f"/static/qrs/{token}.png"
        
        db = conectar_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO permisos_salida (matricula, motivo, salon_origen, salon_destino, token_qr, estatus) 
            VALUES (%s, %s, %s, %s, %s, 'Creado')
        """, (matricula, motivo, salon_orig, salon_dest, token))
        db.commit()
        db.close()
        
        return jsonify({
            "status": "success", 
            "qr_url": qr_url, 
            "token_generado": token
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --- PANEL DIRECTIVO Y PROFESORES ---
@app.route('/admin/dashboard')
def dashboard_directivo():
    if not session.get('logged_in') or session.get('rol') not in ['directivo', 'administrativo', 'programador']:
        return redirect(url_for('login'))
        
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT matricula_clave AS username, nombre, seccion 
            FROM usuarios_sistema 
            WHERE rol = 'alumno'
            ORDER BY nombre ASC
        """)
        lista_alumnos_directivo = cursor.fetchall()
    except:
        lista_alumnos_directivo = []
        
    db.close()
    
    return render_template(
        'panel_directivo.html',
        lista_alumnos_directivo=lista_alumnos_directivo,
        global_asistencias=85,
        global_faltas=15
    )

@app.route('/profesor/dashboard')
def dashboard_profesor():
    if not session.get('logged_in') or session.get('rol') != 'profesor':
        return redirect(url_for('login'))
        
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    clave_profesor = session.get('matricula')
    try:
        cursor.execute("""
            SELECT seccion, materia, hora_inicio AS hora_entrada 
            FROM horarios_clases 
            WHERE clave_profesor = %s
        """, (clave_profesor,))
        lista_clases_profesor = cursor.fetchall()
    except:
        lista_clases_profesor = []
        
    db.close()
    return render_template('panel_profesor.html', lista_clases_profesor=lista_clases_profesor)

@app.route('/registrar_docente', methods=['POST'])
def registrar_docente():
    if not session.get('logged_in') or session.get('rol') not in ['directivo', 'administrativo', 'programador']:
        return jsonify({"status": "error", "message": "No autorizado"})
        
    username = request.form.get('username')
    nombre = request.form.get('nombre')
    correo = request.form.get('correo')
    password = request.form.get('password')
    password_cifrado = generate_password_hash(password)
    
    db = conectar_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            INSERT INTO usuarios_sistema (matricula_clave, nombre, correo, contrasena, rol, primer_ingreso)
            VALUES (%s, %s, %s, %s, 'profesor', 1)
        """, (username, nombre, correo, password_cifrado))
        db.commit()
        db.close()
        return jsonify({"status": "success", "message": "Docente dado de alta correctamente en el sistema."})
    except Exception as e:
        db.close()
        return jsonify({"status": "error", "message": f"Error: {str(e)}"})

@app.route('/admin/asignar_aula', methods=['POST'])
def admin_asignar_aula():
    if not session.get('logged_in') or session.get('rol') not in ['directivo', 'administrativo', 'programador']:
        return jsonify({"status": "error", "message": "No autorizado"})
        
    seccion = request.form.get('seccion').strip()
    edificio = request.form.get('edificio').strip()
    salon = request.form.get('salon').strip()
    
    db = conectar_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            INSERT INTO asignacion_aulas (seccion, edificio, salon) 
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE edificio = VALUES(edificio), salon = VALUES(salon)
        """, (seccion, edificio, salon))
        
        cursor.execute("""
            UPDATE usuarios_sistema 
            SET edificio = %s, salon_fijo = %s 
            WHERE seccion = %s AND rol = 'alumno'
        """, (edificio, salon, seccion))
        
        db.commit()
        db.close()
        return jsonify({"status": "success", "message": f"Sección {seccion} reubicada exitosamente al Edificio {edificio}, Salón {salon}."})
    except Exception as e:
        db.close()
        return jsonify({"status": "error", "message": f"Error al guardar asignación: {str(e)}"})

@app.route('/admin/validar_adelanto/<int:id>', methods=['GET', 'POST'])
def admin_validar_adelanto(id):
    if not session.get('logged_in') or session.get('rol') not in ['directivo', 'administrativo', 'programador']:
        flash('Acceso denegado.', 'danger')
        return redirect(url_for('login'))
        
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM adelantos_clase WHERE id = %s", (id,))
    solicitud = cursor.fetchone()
    
    if request.method == 'POST':
        decision = request.form.get('decision')
        cursor.execute("UPDATE adelantos_clase SET estado_admin = %s WHERE id = %s", (decision, id))
        db.commit()
        db.close()
        flash(f'Solicitud marcada como: {decision}', 'success')
        return redirect(url_for('dashboard_directivo'))
        
    db.close()
    return render_template('validar_adelanto_admin.html', solicitud=solicitud)

@app.route('/profe/validar_adelanto/<int:id>', methods=['GET', 'POST'])
def profe_validar_adelanto(id):
    if not session.get('logged_in') or session.get('rol') != 'profesor':
        flash('Acceso denegado.', 'danger')
        return redirect(url_for('login'))
        
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM adelantos_clase WHERE id = %s", (id,))
    solicitud = cursor.fetchone()
    
    if request.method == 'POST':
        decision = request.form.get('decision')
        if solicitud['estado_admin'] != 'Aprobado':
            flash('La coordinación aún no aprueba este adelanto.', 'warning')
            return redirect(url_for('dashboard_profesor'))
            
        cursor.execute("UPDATE adelantos_clase SET estado_profesor = %s WHERE id = %s", (decision, id))
        db.commit()
        db.close()
        flash(f'Veredicto registrado: {decision}', 'success')
        return redirect(url_for('dashboard_profesor'))
        
    db.close()
    return render_template('validar_adelanto_profe.html', solicitud=solicitud)

@app.route('/crear_traslado_laboratorio', methods=['POST'])
def crear_traslado_laboratorio():
    if not session.get('logged_in') or session.get('rol') != 'profesor':
        return jsonify({"status": "error", "message": "No autorizado"})
        
    seccion = request.form.get('seccion')
    laboratorio = request.form.get('laboratorio')
    profesor = session.get('nombre')
    token_traslado = f"TRASLADO-{seccion}-{random.randint(1000, 9999)}"
    
    try:
        img = qrcode.make(token_traslado)
        img.save(f"static/qrs/{token_traslado}.png")
        db = conectar_db()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO permisos_salida (matricula, motivo, salon_origen, salon_destino, token_qr, estatus)
            VALUES (%s, %s, %s, %s, %s, 'Grupo en Traslado')
        """, (session.get('matricula'), f"Traslado a {laboratorio} por Prof. {profesor}", "Aula Base", laboratorio, token_traslado))
        db.commit()
        db.close()
        return jsonify({"status": "success", "qr_url": f"/static/qrs/{token_traslado}.png", "token": token_traslado})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --- PANEL PROGRAMADOR ---
@app.route('/programador/panel')
def panel_programador():
    if not session.get('logged_in') or session.get('rol') != 'programador':
        flash('Acceso restringido.', 'danger')
        return redirect(url_for('login'))
        
    db = conectar_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("""
            SELECT a.id, a.matricula, u.nombre, a.fecha_hora, a.notas
            FROM asistencias_nfc a
            LEFT JOIN usuarios_sistema u ON a.matricula = u.matricula_clave
            ORDER BY a.fecha_hora DESC LIMIT 100
        """)
        lista_accesos = cursor.fetchall()
    except:
        lista_accesos = []
        
    try:
        cursor.execute("""
            SELECT id_usuario AS id, matricula_clave AS username, nombre, rol, seccion, edificio, salon_fijo AS salon, tarjeta_nfc
            FROM usuarios_sistema
            ORDER BY rol, nombre ASC
        """)
        lista_usuarios = cursor.fetchall()
    except:
        lista_usuarios = []
        
    db.close()
    return render_template('panel_programador.html', lista_accesos=lista_accesos, lista_usuarios=lista_usuarios)

@app.route('/registrar_directivo', methods=['POST'])
def registrar_directivo():
    if not session.get('logged_in') or session.get('rol') != 'programador':
        return jsonify({"status": "error", "message": "No autorizado"})
        
    clave = request.form.get('clave')
    nombre = request.form.get('nombre')
    correo = request.form.get('correo')
    contrasena = request.form.get('contrasena')
    pass_cifrada = generate_password_hash(contrasena)
    
    db = conectar_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            INSERT INTO usuarios_admin (usuario, nombre, correo, contrasena, primer_ingreso)
            VALUES (%s, %s, %s, %s, 1)
        """, (clave, nombre, correo, pass_cifrada))
        db.commit()
        db.close()
        return jsonify({"status": "success", "message": "Directivo registrado correctamente."})
    except Exception as e:
        db.close()
        return jsonify({"status": "error", "message": f"Error: {str(e)}"})

@app.route('/asignar_horario', methods=['POST'])
def asignar_horario():
    if not session.get('logged_in') or session.get('rol') != 'programador':
        return jsonify({"status": "error", "message": "No autorizado"})
        
    seccion = request.form.get('seccion')
    materia = request.form.get('materia')
    hora_entrada = request.form.get('hora_entrada')
    
    db = conectar_db()
    cursor = db.cursor()
    try:
        cursor.execute("""
            INSERT INTO horarios_clases (seccion, materia, hora_inicio)
            VALUES (%s, %s, %s)
        """, (seccion, materia, hora_entrada))
        db.commit()
        db.close()
        return jsonify({"status": "success", "message": "Horario inyectado exitosamente."})
    except Exception as e:
        db.close()
        return jsonify({"status": "error", "message": f"Error: {str(e)}"})

@app.route('/justificar_asistencia', methods=['POST'])
def justificar_asistencia():
    if not session.get('logged_in') or session.get('rol') not in ['programador', 'directivo', 'administrativo']:
        return jsonify({"status": "error", "message": "No autorizado"})
        
    acceso_id = request.form.get('acceso_id')
    nuevo_estado = request.form.get('nuevo_estado', 'Justificado')
    
    db = conectar_db()
    cursor = db.cursor()
    try:
        cursor.execute("UPDATE asistencias_nfc SET notas = %s WHERE id = %s", (nuevo_estado, acceso_id))
        db.commit()
        db.close()
        return jsonify({"status": "success", "message": "Estatus actualizado correctamente."})
    except Exception as e:
        db.close()
        return jsonify({"status": "error", "message": f"Error: {str(e)}"})

@app.route('/admin/vincular_nfc', methods=['POST'])
def vincular_nfc():
    if not session.get('logged_in') or session.get('rol') not in ['programador', 'directivo', 'administrativo']:
        return jsonify({"status": "error", "mensaje": "No autorizado"})
        
    matricula = request.form.get('matricula')
    uid_nfc = request.form.get('uid_nfc')
    
    db = conectar_db()
    cursor = db.cursor()
    try:
        cursor.execute("UPDATE usuarios_sistema SET tarjeta_nfc = %s WHERE matricula_clave = %s", (uid_nfc, matricula))
        db.commit()
        db.close()
        return jsonify({"status": "success", "mensaje": f"Llavero NFC vinculado con éxito."})
    except Exception as e:
        db.close()
        return jsonify({"status": "error", "mensaje": f"Error: {str(e)}"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)