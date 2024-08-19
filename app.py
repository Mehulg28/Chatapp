from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_session import Session
from werkzeug.utils import secure_filename
from supabase import create_client
import os
import uuid
from datetime import datetime, timezone
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
load_dotenv() 

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET')

Session(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False, ping_timeout=300, ping_interval=25)

supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_KEY')
supabase = create_client(supabase_url, supabase_key)

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    userinfo_endpoint='https://www.googleapis.com/oauth2/v3/userinfo',
    client_kwargs={'scope': 'openid email profile'},
    server_metadata_url= 'https://accounts.google.com/.well-known/openid-configuration'
)

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('chat'))
    return render_template('index.html')

@app.route('/login')
def login():
    if 'user' in session:
        return redirect(url_for('chat'))
    google = oauth.create_client('google')
    redirect_uri = url_for('authorize', _external=True)
    return google.authorize_redirect(redirect_uri)

# Authorization route
@app.route('/authorize')
def authorize():
    google = oauth.create_client('google')
    token = google.authorize_access_token()

    userinfo = google.userinfo()

    email = userinfo['email']
    name = userinfo['name']
    
    try:
        # Check if user exists
        user = supabase.table('users').select('*').eq('email', email).execute()
        if not user.data:
            # If user doesn't exist, create a new one
            user = supabase.table('users').insert({'email': email, 'name': name}).execute()
        
        # Ensure we have user data
        if user.data:
            user_data = user.data[0]
            session['user'] = {'id': user_data['id'], 'email': email, 'name': name}
            return redirect(url_for('chat'))
        else:
            return jsonify({"error": "Failed to create or retrieve user"}), 500
    except Exception as e:
        print(f"Error during login: {e}")
        return jsonify({"error": "Login failed", "details": str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/chat')
def chat():
    print(session)
    if 'user' not in session:
        return redirect(url_for('index'))
    
    return render_template('chat.html', user=session['user'])

@app.route('/get_current_user')
def get_current_user():
    return jsonify(session.get('user', {}))

@app.route('/get_messages')
def get_messages():
    try:
        messages = supabase.table('messages').select('*').order('timestamp').execute()
        current_user_email = session['user']['email']
        return jsonify([{
            'id': message['id'],
            'user': message['user_name'],
            'user_id': message['user_id'],
            'user_email': message['user_email'],
            'message': message['content'],
            'type': message['type'],
            'reply_to': message['reply_to'],
            'timestamp': message['timestamp'],
            'edited': message['edited'],
            'is_sent': message['user_email'] == current_user_email
        } for message in messages.data])
    except Exception as e:
        print(f"Error fetching messages: {e}")
        return jsonify({"error": "Failed to fetch messages"}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'})
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'})
    if file:
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        return jsonify({'filename': unique_filename})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    emit('message', {
        'id': str(uuid.uuid4()),
        'user': 'System',
        'message': f"{session['user']['name']} has joined the room.",
        'type': 'text',
        'timestamp': datetime.now(timezone.utc).isoformat()
    }, room=room)

@socketio.on('leave')
def on_leave(data):
    room = data['room']
    leave_room(room)
    emit('message', {
        'id': str(uuid.uuid4()),
        'user': 'System',
        'message': f"{session['user']['name']} has left the room.",
        'type': 'text',
        'timestamp': datetime.now(timezone.utc).isoformat()
    }, room=room)

@socketio.on('message')
def on_message(data):
    if 'user' not in session:
        emit('error', {'message': 'User not authenticated'})
        return

    user = session['user']
    room = data['room']
    content = data.get('content') or data.get('message')
    message_type = data['type']
    reply_to = data.get('reply_to')

    try:
        message = supabase.table('messages').insert({
            'user_id': user['id'],
            'user_name': user['name'],
            'user_email': user['email'],
            'content': content,
            'type': message_type,
            'reply_to': reply_to,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'edited': False
        }).execute()

        if message.data:
            message_data = message.data[0]
            emit('message', {
                'id': message_data['id'],
                'user': user['name'],
                'user_id': user['id'],
                'user_email': user['email'],
                'message': content,
                'type': message_type,
                'reply_to': reply_to,
                'timestamp': message_data['timestamp'],
                'edited': False
            }, room=room)
        else:
            emit('error', {'message': 'Failed to save message'})
    except Exception as e:
        print(f"Error saving message: {e}")
        emit('error', {'message': 'Error saving message'})

@socketio.on('edit_message')
def on_edit_message(data):
    message_id = data['message_id']
    new_content = data['new_content']
    user_id = session['user']['id']

    try:
        message = supabase.table('messages').update({
            'content': new_content,
            'edited': True
        }).eq('id', message_id).eq('user_id', user_id).execute()

        if message.data:
            emit('message_edited', {
                'id': message_id,
                'new_content': new_content,
                'edited': True
            }, broadcast=True)
        else:
            emit('error', {'message': 'Failed to edit message'})
    except Exception as e:
        print(f"Error editing message: {e}")
        emit('error', {'message': 'Error editing message'})

if __name__ == '__main__':
    socketio.run(app, debug=True)
