from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import sqlite3
import threading
import time
from dateutil.relativedelta import relativedelta
import json
import re
from langchain_community.chat_models import ChatCohere
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser

app = Flask(__name__)
CORS(app)

# LangChain Configuration
cohere_api_key = 'your_cohere_api'
llm = ChatCohere(cohere_api_key=cohere_api_key)

# Twilio Configuration
TWILIO_ACCOUNT_SID = 'TWILIO_ACCOUNT_SID'
TWILIO_AUTH_TOKEN = 'TWILIO_AUTH_TOKEN'
TWILIO_WHATSAPP_NUMBER = 'whatsapp:+TWILIO_WHATSAPP_NUMBER'
client_twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Database Setup
def init_db():
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, frequency TEXT, start_date TEXT, end_date TEXT, time TEXT, user_number TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Extract Entities Using LangChain
def extract_entities(text):
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%H:%M")
        
        # Define the prompt template
        prompt_template = ChatPromptTemplate.from_template(
            """Extract task, frequency, start date, end date, and time from: '{user_input}'. 
            Today's date is {current_date}, and the current time is {current_time}. 
            If not mentioned, set time to 9 AM, start date to today, and end date to 9999-12-31. 
            Handle relative dates like 'today', '2 years', 'this year', etc. 
            Return as valid JSON with these exact keys: task, frequency, start_date, end_date, time.
            Example Output:
            {{
                "task": "meeting with team",
                "frequency": "daily",
                "start_date": "2023-10-15",
                "end_date": "2023-10-20",
                "time": "14:00"
            }}"""
        )
        
        # Create the chain
        parser = JsonOutputParser()
        chain = prompt_template | llm | parser
        
        # Invoke the chain
        reminder_data = chain.invoke({
            "user_input": text,
            "current_date": current_date,
            "current_time": current_time
        })
        
        print("Extracted entities:", reminder_data)
        return reminder_data

    except Exception as e:
        print(f"Error extracting entities: {e}")
        return None

# Handle Incoming WhatsApp Messages
@app.route('/webhook', methods=['POST'])
def webhook():
    user_number = request.form['From']
    user_message = request.form['Body'].strip().lower()

    # Handle Greetings
    if user_message in ["hi", "hello", "hey"]:
        response_message = "Hello! How can I assist you today?"
    # Handle Thank You Messages
    elif "thank you" in user_message or "thanks" in user_message:
        response_message = "You're welcome! Let me know if you need anything else."
    # Handle List All Reminders
    elif "list all reminders" in user_message or "give me all reminders" in user_message:
        response_message = list_all_reminders(user_number)
    # Handle Delete Requests
    elif user_message.startswith("delete"):
        task_to_delete = user_message.replace("delete", "").strip()
        response_message = delete_reminder(user_number, task_to_delete)
    # Handle Update Requests
    elif user_message.startswith("update"):
        task_to_update = user_message.replace("update", "").strip()
        response_message = update_reminder(user_number, task_to_update)
    # Handle Reminder Creation
    else:
        reminder = extract_entities(user_message)
        if not reminder:
            response_message = "Sorry, I couldn't understand your reminder. Please try again."
        else:
            # Save reminder to database
            conn = sqlite3.connect('reminders.db')
            c = conn.cursor()
            c.execute('INSERT INTO reminders (task, frequency, start_date, end_date, time, user_number) VALUES (?, ?, ?, ?, ?, ?)',
                      (reminder['task'], reminder['frequency'], reminder['start_date'], reminder['end_date'], reminder['time'], user_number))
            conn.commit()
            conn.close()

            response_message = f"Reminder set: {reminder['task']} at {reminder['time']} on {reminder['start_date']}."

    # Send response back to user
    send_whatsapp_message(user_number, response_message)

    # Return empty response (Twilio expects this)
    return str(MessagingResponse())

# List All Reminders
def list_all_reminders(user_number):
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute('SELECT * FROM reminders WHERE user_number = ?', (user_number,))
    reminders = c.fetchall()
    conn.close()

    if not reminders:
        return "You have no reminders set."

    reminder_list = "\n".join([f"{reminder[1]} at {reminder[5]} on {reminder[3]}" for reminder in reminders])
    return f"Your reminders:\n{reminder_list}"

# Delete Reminder
def delete_reminder(user_number, task_to_delete):
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute('DELETE FROM reminders WHERE user_number = ? AND task LIKE ?', (user_number, f"%{task_to_delete}%"))
    conn.commit()
    conn.close()
    return f"Reminder for '{task_to_delete}' has been deleted."

# Update Reminder
def update_reminder(user_number, task_to_update):
    conn = sqlite3.connect('reminders.db')
    c = conn.cursor()
    c.execute('SELECT * FROM reminders WHERE user_number = ? AND task LIKE ?', (user_number, f"%{task_to_update}%"))
    reminder = c.fetchone()
    if not reminder:
        return f"No reminder found for '{task_to_update}'."
    
    # Extract new details using LangChain
    new_reminder = extract_entities(task_to_update)
    if not new_reminder:
        return "Sorry, I couldn't understand the update. Please try again."

    # Update reminder in database
    c.execute('UPDATE reminders SET task = ?, frequency = ?, start_date = ?, end_date = ?, time = ? WHERE id = ?',
              (new_reminder['task'], new_reminder['frequency'], new_reminder['start_date'], new_reminder['end_date'], new_reminder['time'], reminder[0]))
    conn.commit()
    conn.close()
    return f"Reminder updated: {new_reminder['task']} at {new_reminder['time']} on {new_reminder['start_date']}."

# Send WhatsApp Message
def send_whatsapp_message(to, message):
    client_twilio.messages.create(
        body=message,
        from_=TWILIO_WHATSAPP_NUMBER,
        to=to
    )

# Generate Friendly Reminder Message using LangChain
def generate_reminder_message(reminder):
    prompt_template = ChatPromptTemplate.from_template(
        """We are sending a reminder to the user. The reminder details are: 
        Task: {task}, Time: {time}, Date: {date}. 
        Generate a friendly and human-like reminder message in one sentence without any additional text or formatting."""
    )
    
    chain = prompt_template | llm | StrOutputParser()
    
    message = chain.invoke({
        "task": reminder[1],
        "time": reminder[5],
        "date": reminder[3]
    })
    
    return message.strip()

# Check Reminders and Send Notifications
def check_reminders():
    while True:
        conn = sqlite3.connect('reminders.db')
        c = conn.cursor()
        c.execute('SELECT * FROM reminders')
        reminders = c.fetchall()
        conn.close()

        current_time = datetime.now().strftime('%H:%M')
        for reminder in reminders:
            if reminder[5] == current_time:  # Check if reminder time matches current time
                message = generate_reminder_message(reminder)
                send_whatsapp_message(reminder[6], message)

        time.sleep(60)  # Check every minute

# Run the reminder checker in a separate thread
threading.Thread(target=check_reminders, daemon=True).start()

if __name__ == '__main__':
    app.run(debug=True)
