from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, current_app
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from werkzeug.utils import secure_filename
import os
import json
import uuid
from datetime import datetime, timezone
from flask_migrate import Migrate

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat.db'
app.config['SESSION_TYPE'] = 'filesystem'
CORS(app)
Session(app)
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=False)
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Create UPLOAD_FOLDER if it doesn't exist
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Database models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(80), nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    type = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    reply_to = db.Column(db.Integer, db.ForeignKey('message.id'))
    edited = db.Column(db.Boolean, default=False)

class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(255), nullable=False)
    options = db.Column(db.Text, nullable=False)
    votes = db.Column(db.Text, nullable=False)
    user_votes = db.Column(db.Text, nullable=False)  # New column to track user votes

with app.app_context():
    db.create_all()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']
    name = request.form['name']
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name)
        db.session.add(user)
        db.session.commit()
    session['user'] = {'id': user.id, 'email': email, 'name': name}
    return redirect(url_for('chat'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/chat')
def chat():
    if 'user' not in session:
        return redirect(url_for('index'))
    return render_template('chat.html', user=session['user'])

@app.route('/get_current_user')
def get_current_user():
    if 'user' in session:
        return jsonify(session['user'])
    else:
        return jsonify({}), 401

@app.route('/get_messages')
def get_messages():
    messages = Message.query.order_by(Message.timestamp).all()
    current_user_email = session['user']['email']
    return jsonify([{
        'id': message.id,
        'user': db.session.get(User, message.user_id).name,
        'user_id': message.user_id,
        'user_email': db.session.get(User, message.user_id).email,
        'message': message.content,
        'type': message.type,
        'reply_to': message.reply_to,
        'timestamp': message.timestamp.isoformat(),
        'edited': message.edited,
        'is_sent': db.session.get(User, message.user_id).email == current_user_email
    } for message in messages])

@app.route('/get_polls')
def get_polls():
    polls = Poll.query.all()
    return jsonify([{
        'poll_id': poll.id,
        'poll_data': {
            'question': poll.question,
            'options': poll.options.split(','),
            'votes': [int(v) for v in poll.votes.split(',')]
        }
    } for poll in polls])

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        app.logger.error("No file part in the request")
        return jsonify({'error': 'No file part'})
    file = request.files['file']
    if file.filename == '':
        app.logger.error("No selected file")
        return jsonify({'error': 'No selected file'})
    if file:
        try:
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4()}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            app.logger.info(f"File saved successfully: {file_path}")
            return jsonify({'filename': unique_filename})
        except Exception as e:
            app.logger.error(f"Error saving file: {str(e)}")
            return jsonify({'error': 'Error saving file'}), 500
        
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
    room = data['room']
    user = db.session.get(User, session['user']['id'])
    content = data.get('content') or data.get('message')  # Handle both 'content' and 'message' keys
    message = Message(
        user_id=user.id,
        content=content,
        type=data['type'],
        reply_to=data.get('reply_to')
    )
    db.session.add(message)
    db.session.commit()
    message_data = {
        'id': message.id,
        'user': user.name,
        'user_id': user.id,
        'user_email': user.email,
        'message': content,  # Use 'content' here
        'type': data['type'],
        'reply_to': data.get('reply_to'),
        'timestamp': message.timestamp.isoformat(),
        'edited': False
    }
    emit('message', message_data, room=room)

@socketio.on('edit_message')
def on_edit_message(data):
    message_id = data['message_id']
    new_content = data['new_content']
    message = Message.query.get(message_id)
    if message and message.user_id == session['user']['id']:
        message.content = new_content
        message.edited = True
        db.session.commit()
        emit('message_edited', {
            'id': message.id,
            'new_content': new_content,
            'edited': True
        }, broadcast=True)

@socketio.on('create_poll')
def on_create_poll(data):
    poll = Poll(
        question=data['question'],
        options=','.join(data['options']),
        votes=','.join(['0'] * len(data['options'])),
        user_votes='{}'  # Initialize as empty JSON string
    )
    db.session.add(poll)
    db.session.commit()
    emit('new_poll', {'poll_id': poll.id, 'poll_data': {
        'question': poll.question,
        'options': poll.options.split(','),
        'votes': [int(v) for v in poll.votes.split(',')]
    }}, room=data['room'])

@socketio.on('vote')
def on_vote(data):
    poll = Poll.query.get(data['poll_id'])
    user_id = session['user']['id']
    if poll:
        votes = [int(v) for v in poll.votes.split(',')]
        user_votes = json.loads(poll.user_votes)
        
        # Check if user has already voted
        if str(user_id) in user_votes:
            previous_vote = user_votes[str(user_id)]
            if previous_vote == data['option']:
                # Retract vote
                votes[previous_vote] -= 1
                del user_votes[str(user_id)]
            else:
                # Change vote
                votes[previous_vote] -= 1
                votes[data['option']] += 1
                user_votes[str(user_id)] = data['option']
        else:
            # New vote
            votes[data['option']] += 1
            user_votes[str(user_id)] = data['option']
        
        poll.votes = ','.join(map(str, votes))
        poll.user_votes = json.dumps(user_votes)
        db.session.commit()
        emit('update_poll', {'poll_id': poll.id, 'poll_data': {
            'question': poll.question,
            'options': poll.options.split(','),
            'votes': votes
        }}, room=data['room'])

@socketio.on('add_poll_option')
def add_poll_option(data):
    poll = Poll.query.get(data['poll_id'])
    if poll:
        options = poll.options.split(',')
        votes = [int(v) for v in poll.votes.split(',')]
        options.append(data['new_option'])
        votes.append(0)
        poll.options = ','.join(options)
        poll.votes = ','.join(map(str, votes))
        db.session.commit()
        emit('update_poll', {'poll_id': poll.id, 'poll_data': {
            'question': poll.question,
            'options': options,
            'votes': votes
        }}, room=data['room'])

@socketio.on('delete_poll_option')
def delete_poll_option(data):
    poll = Poll.query.get(data['poll_id'])
    if poll:
        options = poll.options.split(',')
        votes = [int(v) for v in poll.votes.split(',')]
        index = data['option_index']
        if 0 <= index < len(options):
            del options[index]
            del votes[index]
            poll.options = ','.join(options)
            poll.votes = ','.join(map(str, votes))
            user_votes = json.loads(poll.user_votes)
            # Remove votes for deleted option
            user_votes = {k: v for k, v in user_votes.items() if int(v) != index}
            # Adjust remaining votes
            user_votes = {k: str(int(v) - 1) if int(v) > index else v for k, v in user_votes.items()}
            poll.user_votes = json.dumps(user_votes)
            db.session.commit()
            emit('update_poll', {'poll_id': poll.id, 'poll_data': {
                'question': poll.question,
                'options': options,
                'votes': votes
            }}, room=data['room'])

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

if __name__ == '__main__':
    socketio.run(app, debug=True)