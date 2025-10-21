from flask import Flask, request, jsonify, send_from_directory, session
import requests
import os
import json
from werkzeug.utils import secure_filename
import threading
import time
import google.generativeai as genai
import base64
from PIL import Image
import io
import logging
import re

# Suppress Google Generative AI warnings
logging.getLogger('absl').setLevel(logging.ERROR)
logging.getLogger('google').setLevel(logging.ERROR)

def clean_ai_response(response):
    """Clean AI response to ensure it's valid HTML without markdown."""
    response = response.strip()
    # Remove markdown code block markers
    if response.startswith('```html'):
        response = response[7:].strip()
    if response.endswith('```'):
        response = response[:-3].strip()
    # Remove full HTML structure if present
    if response.startswith('<html>') or response.startswith('<!DOCTYPE html>'):
        # Extract content between <body> and </body> if present
        body_match = re.search(r'<body[^>]*>(.*?)</body>', response, re.DOTALL | re.IGNORECASE)
        if body_match:
            response = body_match.group(1).strip()
        else:
            # Remove <html>, <head>, <body> tags
            response = re.sub(r'</?html[^>]*>', '', response, flags=re.IGNORECASE)
            response = re.sub(r'</?head[^>]*>', '', response, flags=re.IGNORECASE)
            response = re.sub(r'</?body[^>]*>', '', response, flags=re.IGNORECASE)
            response = re.sub(r'</?title[^>]*>.*?</title>', '', response, flags=re.IGNORECASE | re.DOTALL)
    # Remove markdown bold/italic
    response = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', response)
    response = re.sub(r'\*(.*?)\*', r'<em>\1</em>', response)
    response = re.sub(r'_(.*?)_', r'<em>\1</em>', response)
    # Ensure paragraphs are wrapped in <p> tags if not already
    if not response.startswith('<p>') and not response.startswith('<'):
        # Split by double newlines and wrap in <p>
        paragraphs = response.split('\n\n')
        response = ''.join(f'<p>{p.strip()}</p>' for p in paragraphs if p.strip())
    return response

app = Flask(__name__)
logging.getLogger('werkzeug').setLevel(logging.CRITICAL)
logging.getLogger('grpc').setLevel(logging.CRITICAL)  # Suppress gRPC warnings
app.logger.disabled = True
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_secret_key_here')

# Persistent storage for API keys
KEYS_FILE = 'api_keys.json'

@app.before_request
def load_session_keys():
    if 'printify_key' not in session and 'printify_key' in saved_keys:
        session['printify_key'] = saved_keys['printify_key']
    if 'openai_key' not in session and 'openai_key' in saved_keys:
        session['openai_key'] = saved_keys['openai_key']
    if 'gemini_key' not in session and 'gemini_key' in saved_keys:
        session['gemini_key'] = saved_keys['gemini_key']

def load_keys():
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_keys(keys):
    with open(KEYS_FILE, 'w') as f:
        json.dump(keys, f)

# Load keys on startup (but don't update session yet)
saved_keys = load_keys()

# Configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Global variables for progress
progress = {'status': 'idle', 'current': 0, 'total': 0, 'message': ''}
cancel_operation = False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')  

@app.route('/styles.css')
def styles():
    return send_from_directory('.', 'styles.css')

@app.route('/app.js')
def script():
    return send_from_directory('.', 'app.js')

@app.route('/api/stores', methods=['GET'])
def get_stores():
    auth_header = request.headers.get('Authorization')
    api_key = auth_header.replace('Bearer ', '') if auth_header else session.get('printify_key')
    if not api_key:
        return jsonify({'error': 'API key required'}), 401
    # Fetch from Printify
    headers = {'Authorization': f'Bearer {api_key}'}
    try:
        response = requests.get('https://api.printify.com/v1/shops.json', headers=headers)
        if response.status_code == 200:
            shops = response.json()
            return jsonify([{'id': shop['id'], 'name': shop['title']} for shop in shops])
        else:
            return jsonify({'error': f'Failed to fetch stores: {response.text}'}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/products', methods=['GET'])
def get_products():
    store_id = request.args.get('store_id')
    auth_header = request.headers.get('Authorization')
    api_key = auth_header.replace('Bearer ', '') if auth_header else session.get('printify_key')
    if not api_key:
        return jsonify({'error': 'API key required'}), 401
    # Fetch from Printify
    headers = {'Authorization': f'Bearer {api_key}'}
    try:
        response = requests.get(f'https://api.printify.com/v1/shops/{store_id}/products.json', headers=headers)
        if response.status_code == 200:
            products = response.json()['data']
            return jsonify([{'id': prod['id'], 'title': prod['title']} for prod in products])
        else:
            return jsonify({'error': f'Failed to fetch products: {response.text}'}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'No files'}), 400
    files = request.files.getlist('files')
    uploaded = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(str(file.filename))
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            uploaded.append(filename)
    return jsonify({'uploaded': uploaded})

@app.route('/api/delete_file', methods=['POST'])
def delete_file():
    data = request.json
    filename = data.get('filename')
    if not filename:
        return jsonify({'error': 'Filename not provided'}), 400

    try:
        secure_name = secure_filename(str(filename))
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_name)
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({'success': True, 'message': f'File {secure_name} deleted.'})
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/create_products', methods=['POST'])
def create_products():
    data = request.json
    # Extract data: images, placement_mode, store_id, product_id, rules
    images = data['images']
    placement_mode = data['placement_mode']
    store_id = data['store_id']
    product_id = data['product_id']
    rules = data['rules']
    rules['custom_html'] = data.get('custom_html', rules.get('custom_html', ''))
    rules['api_key'] = data.get('api_key')
    rules['openai_key'] = data.get('openai_key')
    rules['gemini_key'] = data.get('gemini_key')

    # Start background thread for creation
    threading.Thread(target=create_products_background, args=(images, placement_mode, store_id, product_id, rules)).start()
    return jsonify({'message': 'Creation started'})

def log_message(message, log_type='info'):
    global progress
    progress['message'] = message

def create_products_background(images, placement_mode, store_id, product_id, rules):
    global progress, cancel_operation
    progress['status'] = 'working'
    progress['total'] = len(images)
    progress['current'] = 0
    cancel_operation = False

    api_key = rules.get('api_key')
    gemini_key = rules.get('gemini_key')

    if not api_key:
        progress['status'] = 'error'
        log_message('Printify API key required', 'error')
        return

    headers = {'Authorization': f'Bearer {api_key}'}
    log_message(f'Using API key: {api_key[:10]}...')

    log_message(f'Fetching example product (ID: {product_id}) from store {store_id}...')
    response = requests.get(f'https://api.printify.com/v1/shops/{store_id}/products/{product_id}.json', headers=headers)
    
    if response.status_code != 200:
        progress['status'] = 'error'
        log_message(f'Failed to fetch example product: {response.text}', 'error')
        return
        
    example_product = response.json()
    rules['example_title'] = example_product.get('title', '')
    rules['example_desc'] = example_product.get('description', '')
    rules['example_tags'] = example_product.get('tags', [])
    log_message(f'Example product fetched: {example_product.get("title", "Unknown")}')

    for i, img in enumerate(images):
        if cancel_operation:
            progress['status'] = 'cancelled'
            log_message('Operation cancelled by user', 'info')
            return
            
        progress['current'] = i + 1
        log_message(f'Processing image {i+1}/{len(images)}: {img}')

        secure_img = secure_filename(str(img))
        img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)

        if not os.path.exists(img_path):
            progress['status'] = 'error'
            log_message(f"File not found: {secure_img}", 'error')
            return

        log_message(f'Uploading {secure_img} to Printify...')
        with open(img_path, 'rb') as f:
            file_contents = base64.b64encode(f.read()).decode('utf-8')
        
        try:
            upload_response = requests.post('https://api.printify.com/v1/uploads/images.json', headers=headers, json={'file_name': secure_img, 'contents': file_contents})
            upload_response.raise_for_status()
            image_id = upload_response.json()['id']
            log_message(f'Uploaded image ID: {image_id}')
        except requests.exceptions.RequestException as e:
            progress['status'] = 'error'
            log_message(f"Failed to upload {img}: {e}", 'error')
            return

        provider = rules.get('ai_provider', 'openai')
        key = gemini_key if provider == 'gemini' else rules.get('openai_key')
        
        log_message(f'Generating title for {img} using {provider}...')
        title = generate_content('title', rules, key, img, provider)

        log_message(f'Generating description for {img} using {provider}...')
        description = generate_content('description', rules, key, img, provider)

        log_message(f'Generating tags for {img} using {provider}...')
        tags = generate_content('tags', rules, key, img, provider)
        log_message(f'Generated content - Title: {title}')
        log_message(f'Description: {description}')
        log_message(f'Tags: {tags}')

        print_areas = example_product.get('print_areas', [])
        for area in print_areas:
            for placeholder in area.get('placeholders', []):
                placeholder['images'] = [{'id': image_id, 'x': 0.5, 'y': 0.5, 'scale': 1.0, 'angle': 0}]

        product_data = {
            'title': title,
            'description': description,
            'tags': tags,
            'variants': example_product['variants'],
            'print_provider_id': example_product['print_provider_id'],
            'blueprint_id': example_product['blueprint_id'],
            'print_areas': print_areas
        }
        
        log_message(f'Creating product for {img}...')
        try:
            create_response = requests.post(f'https://api.printify.com/v1/shops/{store_id}/products.json', headers=headers, json=product_data)
            create_response.raise_for_status()
            product_id_created = create_response.json().get('id')
            log_message(f'Successfully created product ID: {product_id_created}')
        except requests.exceptions.RequestException as e:
            progress['status'] = 'error'
            log_message(f"Failed to create product for {img}: {e}", 'error')
            return

    progress['status'] = 'completed'
    log_message('All products created successfully!', 'info')

def generate_content(type, rules, key, img, provider='openai'):
    # Get image description for AI prompts
    image_description = ""
    if key and (type == 'title' and rules['title_source'] == 'ai' or type == 'description' and rules['desc_source'] == 'ai' or type == 'tags' and rules['tag_source'] == 'ai'):
        image_description = analyze_image(img, key, provider)
    elif provider == 'ollama' and (type == 'title' and rules['title_source'] == 'ai' or type == 'description' and rules['desc_source'] == 'ai' or type == 'tags' and rules['tag_source'] == 'ai'):
        image_description = analyze_image_ollama(img, rules)

    # Use AI if key provided and source is AI
    if type == 'title' and rules['title_source'] == 'ai' and (key or provider == 'ollama'):
        if provider == 'gemini' and key:
            try:
                genai.configure(api_key=key)
                generation_config = genai.types.GenerationConfig(
                    max_output_tokens=60,
                    temperature=0.7,
                )
                model = genai.GenerativeModel('models/gemini-2.0-flash', generation_config=generation_config)
                secure_img = secure_filename(str(img))
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    prompt = "Generate exactly one creative title for a print-on-demand product based on this image. Keep it under 60 characters. Make it catchy and appealing. If there is text in the design, try to use that in the title. Return only the title, nothing else."
                    response = model.generate_content([prompt, pil_image])
                    ai_title = response.text.strip()
                    # Ensure it's under 60 chars and take first line if multiple
                    ai_title = ai_title.split('\n')[0].strip()
                    # Remove quotation marks
                    ai_title = ai_title.strip('"').strip("'")
                else:
                    ai_title = img.rsplit('.', 1)[0]  # Fallback
            except Exception as e:
                ai_title = img.rsplit('.', 1)[0]  # Fallback
        elif provider == 'openai' and key:
            try:
                import openai
                client = openai.OpenAI(api_key=key)
                prompt = f"Generate a creative title for a print-on-demand product based on this image. Keep it under 60 characters. Make it catchy and appealing. If there is text in the design, try to use that in the title."
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{get_image_base64(img)}"}}
                            ]
                        }
                    ],
                    max_tokens=50,
                    temperature=0.7,
                )
                ai_title = response.choices[0].message.content.strip()
            except Exception as e:
                ai_title = img.rsplit('.', 1)[0]  # Fallback
        elif provider == 'ollama':
            try:
                secure_img = secure_filename(str(img))
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    pil_image.save(buffer, format='PNG')
                    resized_data = buffer.getvalue()
                    image_b64 = base64.b64encode(resized_data).decode('utf-8')
                    payload = {
                        "model": rules.get('ollama_model', 'llava'),
                        "prompt": "If there is text in the image, describe only that text in 1-3 words. If there is no text, describe the image in 1-3 words. Return only the description, nothing else.",
                        "images": [image_b64],
                        "stream": False
                    }
                    response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                    if response.status_code == 200:
                        ai_title = response.json().get('response', 'No response').strip()
                        # Clean up title
                        ai_title = ai_title.replace('\n', ' ').strip()
                        ai_title = ai_title.strip('"').strip("'")
                    else:
                        ai_title = img.rsplit('.', 1)[0]  # Fallback
                else:
                    ai_title = img.rsplit('.', 1)[0]  # Fallback
            except Exception as e:
                ai_title = img.rsplit('.', 1)[0]  # Fallback
        # Apply template and custom text
        template = rules.get('title_template', '[AI-Generated Title]')
        custom_text = rules.get('custom_title_text', '')
        title = template.replace('[AI-Generated Title]', ai_title).replace('[Custom Text]', custom_text)
        title = title[:60]  # Ensure the final title is under 60 characters
        return title
    elif type == 'description' and rules['desc_source'] == 'ai' and (key or provider == 'ollama'):
        prompt = "Generate a compelling product description for a print-on-demand item based on this image. Make it engaging and highlight the unique take on the product. If there is text in the design, try to incorporate that information into the description. Output in valid HTML format using <p> for paragraphs, <strong> for bold text, <em> for italic text, and other basic HTML tags as appropriate. Do not include <html>, <head>, or <body> tags - just the content. Do not use any markdown syntax such as **, *, _, or any other non-HTML formatting."
        if rules.get('influencer_phrases'):
            prompt += f" Incorporate the following style or perspective: {rules['influencer_phrases']}."
        if provider == 'gemini':
            try:
                genai.configure(api_key=key)
                generation_config = genai.types.GenerationConfig(
                    max_output_tokens=400,
                    temperature=0.7,
                )
                model = genai.GenerativeModel('models/gemini-2.0-flash', generation_config=generation_config)
                secure_img = secure_filename(img)
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    response = model.generate_content([prompt, pil_image])
                    ai_desc = response.text.strip()
                else:
                    ai_desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
            except Exception as e:
                ai_desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
            desc = clean_ai_response(ai_desc)
            custom_html = rules.get('custom_html', '')
            return desc + custom_html
        elif provider == 'openai':
            try:
                import openai
                client = openai.OpenAI(api_key=key)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{get_image_base64(img)}"}}
                            ]
                        }
                    ],
                    max_tokens=200,
                    temperature=0.7,
                    timeout=30,
                )
                ai_desc = response.choices[0].message.content.strip()
                # Enforce paragraph count
                paragraphs = rules.get('paragraphs', 1)
                ai_desc = '\n\n'.join(ai_desc.split('\n\n')[:paragraphs])
                desc = clean_ai_response(ai_desc)
            except Exception as e:
                desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
            custom_html = rules.get('custom_html', '')
            return desc + custom_html
        elif provider == 'ollama':
            try:
                secure_img = secure_filename(img)
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    pil_image.save(buffer, format='PNG')
                    resized_data = buffer.getvalue()
                    image_b64 = base64.b64encode(resized_data).decode('utf-8')
                    payload = {
                        "model": rules.get('ollama_model', 'llava'),
                        "prompt": prompt,
                        "images": [image_b64],
                        "stream": False
                    }
                    response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                    if response.status_code == 200:
                        desc = clean_ai_response(response.json().get('response', 'No response').strip())
                    else:
                        desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
                else:
                    desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
            except Exception as e:
                desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
            custom_html = rules.get('custom_html', '')
            return desc + custom_html
    elif type == 'tags' and rules['tag_source'] == 'ai' and (key or provider == 'ollama'):
        if provider == 'gemini':
            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel('models/gemini-2.0-flash')
                secure_img = secure_filename(img)
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    prompt = "Generate 10 relevant tags for a custom print-on-demand product based on this image. Make them SEO-friendly and appealing. Return as a comma-separated list."
                    response = model.generate_content([prompt, pil_image])
                    tags_str = response.text.strip()
                    return [tag.strip() for tag in tags_str.split(',') if tag.strip()]
                else:
                    return ['custom', 'print-on-demand', 'artwork']  # Fallback
            except Exception as e:
                return ['custom', 'print-on-demand', 'artwork']  # Fallback
        elif provider == 'openai':
            try:
                import openai
                client = openai.OpenAI(api_key=key)
                prompt = f"Generate 10 relevant tags for a custom print-on-demand product based on this image description: {image_description}. Make them SEO-friendly and appealing. Return as a comma-separated list."
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{get_image_base64(img)}"}}
                            ]
                        }
                    ],
                    max_tokens=100,
                    temperature=0.7,
                )
                tags_str = response.choices[0].message.content.strip()
                return [tag.strip() for tag in tags_str.split(',') if tag.strip()]
            except Exception as e:
                return ['custom', 'print-on-demand', 'artwork']  # Fallback
        elif provider == 'ollama':
            try:
                secure_img = secure_filename(img)
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    pil_image.save(buffer, format='PNG')
                    resized_data = buffer.getvalue()
                    image_b64 = base64.b64encode(resized_data).decode('utf-8')
                    payload = {
                        "model": rules.get('ollama_model', 'llava'),
                        "prompt": "Generate 10 relevant tags for a custom print-on-demand product based on this image. Make them SEO-friendly and appealing. Return as a comma-separated list.",
                        "images": [image_b64],
                        "stream": False
                    }
                    response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                    if response.status_code == 200:
                        tags_str = response.json().get('response', 'No response').strip()
                        # Clean up tags by removing quotes and extra spaces
                        tags_str = tags_str.replace('"', '').replace("'", '').strip()
                        tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
                        return tags[:10]  # Limit to 10 tags
                    else:
                        return ['custom', 'print-on-demand', 'artwork']  # Fallback
                else:
                    return ['custom', 'print-on-demand', 'artwork']  # Fallback
            except Exception as e:
                return ['custom', 'print-on-demand', 'artwork']  # Fallback

    # Local fallback: use example product content
    if type == 'title':
        if rules['title_source'] == 'filename':
            return img.rsplit('.', 1)[0]
        else:
            return rules.get('example_title', img.rsplit('.', 1)[0])
    elif type == 'description':
        if rules['desc_source'] == 'copy':
            return rules.get('example_desc', '')
        else:
            return rules.get('example_desc', "A unique print-on-demand product featuring custom artwork.")
    elif type == 'tags':
        if rules['tag_source'] == 'copy':
            return rules.get('example_tags', [])
        else:
            return rules.get('example_tags', ['custom', 'print-on-demand', 'artwork'])
    return ''

def analyze_image(img, key, provider):
    """Analyze the image and return a description."""
    secure_img = secure_filename(str(img))
    img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
    if not os.path.exists(img_path):
        return "Image not found"

    try:
        if provider == 'gemini':
            genai.configure(api_key=key)
            model = genai.GenerativeModel('models/gemini-2.0-flash')
            with open(img_path, 'rb') as f:
                image_data = f.read()
            image = Image.open(io.BytesIO(image_data))
            prompt = "Describe this image in detail, focusing on the main subject, colors, style, and any text or elements that would be relevant for creating a print-on-demand product."
            response = model.generate_content([prompt, image])
            return response.text.strip()
        elif provider == 'openai':
            import openai
            client = openai.OpenAI(api_key=key)
            with open(img_path, 'rb') as f:
                image_data = f.read()
            pil_image = Image.open(io.BytesIO(image_data))
            # Resize to max 1024x1024 to reduce size
            max_size = (1024, 1024)
            pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            pil_image.save(buffer, format='PNG')
            resized_data = buffer.getvalue()
            image_data = base64.b64encode(resized_data).decode('utf-8')
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe this image in detail, focusing on the main subject, colors, style, and any text or elements that would be relevant for creating a print-on-demand product."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}}
                        ]
                    }
                ],
                max_tokens=200,
                temperature=0.7,
                timeout=30,
            )
            return response.choices[0].message.content.strip()
    except Exception as e:
        return "Custom artwork image"

def analyze_image_ollama(img, rules):
    """Analyze the image using Ollama local model."""
    secure_img = secure_filename(str(img))
    img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
    if not os.path.exists(img_path):
        return "Image not found"

    try:
        with open(img_path, 'rb') as f:
            image_data = f.read()
        pil_image = Image.open(io.BytesIO(image_data))
        # Resize to max 1024x1024 to reduce size
        max_size = (1024, 1024)
        pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        pil_image.save(buffer, format='PNG')
        resized_data = buffer.getvalue()
        image_b64 = base64.b64encode(resized_data).decode('utf-8')
        payload = {
            "model": rules.get('ollama_model', 'llava'),
            "prompt": "Describe this image in detail, focusing on the main subject, colors, style, and any text or elements that would be relevant for creating a print-on-demand product.",
            "images": [image_b64],
            "stream": False
        }
        response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
        if response.status_code == 200:
            return response.json().get('response', 'No response')
        else:
            return "Error analyzing image with Ollama"
    except Exception as e:
        return "Custom artwork image"

def get_image_base64(img):
    """Get base64 encoded image for OpenAI API."""
    secure_img = secure_filename(str(img))
    img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
    if not os.path.exists(img_path):
        return ""
    with open(img_path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')

@app.route('/api/progress', methods=['GET'])
def get_progress():
    return jsonify(progress)

@app.route('/api/cancel', methods=['POST'])
def cancel_operation():
    global cancel_operation
    cancel_operation = True
    log_message('Cancel operation requested by user', 'info')
    return jsonify({'message': 'Operation cancelled'})

@app.route('/api/generate_title', methods=['POST'])
def generate_title():
    data = request.json
    provider = data.get('provider', 'openai')
    key = data.get(f'{provider}_key')
    if provider != 'ollama' and not key:
        return jsonify({'error': f'{provider.capitalize()} API key required'}), 400

    try:
        mode = data.get('mode', 'simple')
        segments = data.get('segments', 1)
        custom_text = data.get('custom_title_text', '')
        template = data.get('template', '[AI-Generated Title] [Custom Text]')
        image_path = data.get('image_path', '')

        if mode == 'simple':
            prompt = "Generate a creative title for a custom print-on-demand product. Keep it under 60 characters. Make it catchy and appealing."
        else:
            prompt = f"Generate a compound title with {segments} segments for a custom print-on-demand product. Make it creative and appealing."

        if image_path:
            if provider == 'gemini':
                genai.configure(api_key=key)
                model = genai.GenerativeModel('models/gemini-2.0-flash')
                secure_img = secure_filename(str(image_path))
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    response = model.generate_content([prompt, pil_image])
                    ai_title = response.text.strip()
                else:
                    ai_title = "Image not found"
            elif provider == 'openai':
                import openai
                client = openai.OpenAI(api_key=key)
                secure_img = secure_filename(str(image_path))
                img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        image_data = f.read()
                    pil_image = Image.open(io.BytesIO(image_data))
                    # Resize to max 1024x1024 to reduce size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    pil_image.save(buffer, format='PNG')
                    resized_data = buffer.getvalue()
                    image_data = base64.b64encode(resized_data).decode('utf-8')
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}}
                                ]
                            }
                        ],
                        max_tokens=50,
                        temperature=0.7,
                        timeout=30,
                    )
                    ai_title = response.choices[0].message.content.strip()
                    # Remove quotation marks
                    ai_title = ai_title.strip('"').strip("'")
                else:
                    ai_title = "Image not found"
            elif provider == 'ollama':
                try:
                    secure_img = secure_filename(image_path)
                    img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                    if secure_img and os.path.isfile(img_path):
                        with open(img_path, 'rb') as f:
                            image_data = f.read()
                        pil_image = Image.open(io.BytesIO(image_data))
                        # Resize to max 1024x1024 to reduce size
                        max_size = (1024, 1024)
                        pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                        buffer = io.BytesIO()
                        pil_image.save(buffer, format='PNG')
                        resized_data = buffer.getvalue()
                        image_b64 = base64.b64encode(resized_data).decode('utf-8')
                        payload = {
                            "model": rules.get('ollama_model', 'llava'),
                            "prompt": prompt,
                            "images": [image_b64],
                            "stream": False
                        }
                        response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                        if response.status_code == 200:
                            ai_title = response.json().get('response', 'No response').strip()
                            # Clean up title
                            ai_title = ai_title.replace('\n', ' ').strip()
                            ai_title = ai_title.strip('"').strip("'")
                        else:
                            ai_title = "Image not found"
                    else:
                        ai_title = "Image not found"
                except Exception as e:
                    ai_title = "Ollama error"
        else:
            # Fallback to text-only
            if provider == 'gemini':
                genai.configure(api_key=key)
                model = genai.GenerativeModel('models/gemini-2.0-flash')
                response = model.generate_content(prompt)
                ai_title = response.text.strip()
            elif provider == 'openai':
                import openai
                client = openai.OpenAI(api_key=key)
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=50,
                    temperature=0.7,
                )
                ai_title = response.choices[0].message.content.strip()
                # Remove quotation marks
                ai_title = ai_title.strip('"').strip("'")
            elif provider == 'ollama':
                try:
                    secure_img = secure_filename(image_path)
                    img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                    if secure_img and os.path.isfile(img_path):
                        with open(img_path, 'rb') as f:
                            image_data = f.read()
                        pil_image = Image.open(io.BytesIO(image_data))
                        # Resize to max 1024x1024 to reduce size
                        max_size = (1024, 1024)
                        pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                        buffer = io.BytesIO()
                        pil_image.save(buffer, format='PNG')
                        resized_data = buffer.getvalue()
                        image_b64 = base64.b64encode(resized_data).decode('utf-8')
                        payload = {
                            "model": rules.get('ollama_model', 'llava'),
                            "prompt": prompt,
                            "images": [image_b64],
                            "stream": False
                        }
                        response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                        if response.status_code == 200:
                            ai_title = response.json().get('response', 'No response').strip()
                            # Clean up title
                            ai_title = ai_title.replace('\n', ' ').strip()
                            ai_title = ai_title.strip('"').strip("'")
                        else:
                            ai_title = "Image not found"
                    else:
                        ai_title = "Image not found"
                except Exception as e:
                    ai_title = "Ollama error"

        title = template.replace('[AI-Generated Title]', ai_title).replace('[Custom Text]', custom_text)
        title = title[:60]  # Ensure the final title is under 60 characters
        return jsonify({'title': title})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate_description', methods=['POST'])
def generate_description():
    data = request.json
    provider = data.get('provider', 'openai')
    key = data.get(f'{provider}_key')
    if not key:
        return jsonify({'error': f'{provider.capitalize()} API key required'}), 400

    try:
        paragraphs = data.get('paragraphs', 1)
        custom_html = data.get('custom_html', '')
        influencer_phrases = data.get('influencer_phrases', '')
        image_path = data.get('image_path', '')

        prompt = f"Generate a compelling product description based on this image. Write {paragraphs} paragraph(s). Tap into the emotional or thematic message behind the design. Use intriguing, appealing language and incorporate any text from the design. Output in valid HTML format using <p> for paragraphs, <strong> for bold text, <em> for italic text, and other basic HTML tags as appropriate. Do not include <html>, <head>, or <body> tags - just the content. Do not use any markdown syntax such as **, *, _, or any other non-HTML formatting."
        if influencer_phrases:
            prompt += f" Incorporate the following style or perspective: {influencer_phrases}."

        if image_path:
            secure_img = secure_filename(image_path)
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
            if os.path.exists(img_path):
                with open(img_path, 'rb') as f:
                    image_data = f.read()
                pil_image = Image.open(io.BytesIO(image_data))
                # Resize to max 1024x1024 to reduce size
                max_size = (1024, 1024)
                pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                if provider == 'gemini':
                    genai.configure(api_key=key)
                    model = genai.GenerativeModel('models/gemini-2.0-flash')
                    response = model.generate_content([prompt, pil_image])
                    ai_desc = response.text.strip()
                    # Enforce paragraph count
                    paragraphs = data.get('paragraphs', 1)
                    ai_desc = '\n\n'.join(ai_desc.split('\n\n')[:paragraphs])
                elif provider == 'openai':
                    import openai
                    client = openai.OpenAI(api_key=key)
                    buffer = io.BytesIO()
                    pil_image.save(buffer, format='PNG')
                    resized_data = buffer.getvalue()
                    image_data = base64.b64encode(resized_data).decode('utf-8')
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}}
                                ]
                            }
                        ],
                        max_tokens=200,
                        temperature=0.7,
                        timeout=30,
                    )
                    ai_desc = clean_ai_response(response.choices[0].message.content.strip())
            else:
                ai_desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
        else:
            if provider == 'gemini':
                genai.configure(api_key=key)
                model = genai.GenerativeModel('models/gemini-2.0-flash')
                response = model.generate_content(prompt)
                ai_desc = response.text.strip()
                # Enforce paragraph count
                paragraphs = data.get('paragraphs', 1)
                ai_desc = '\n\n'.join(ai_desc.split('\n\n')[:paragraphs])
            elif provider == 'openai':
                import openai
                client = openai.OpenAI(api_key=key)
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0.7,
                    timeout=30,
                )
                ai_desc = clean_ai_response(response.choices[0].message.content.strip())
            elif provider == 'ollama':
                try:
                    secure_img = secure_filename(image_path)
                    img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
                    if os.path.exists(img_path):
                        with open(img_path, 'rb') as f:
                            image_data = f.read()
                        pil_image = Image.open(io.BytesIO(image_data))
                        # Resize to max 1024x1024 to reduce size
                        max_size = (1024, 1024)
                        pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                        buffer = io.BytesIO()
                        pil_image.save(buffer, format='PNG')
                        resized_data = buffer.getvalue()
                        image_b64 = base64.b64encode(resized_data).decode('utf-8')
                        payload = {
                            "model": rules.get('ollama_model', 'llava'),
                            "prompt": prompt,
                            "images": [image_b64],
                            "stream": False
                        }
                        response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                        if response.status_code == 200:
                            ai_desc = clean_ai_response(response.json().get('response', 'No response').strip())
                        else:
                            ai_desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
                    else:
                        ai_desc = "A unique print-on-demand product featuring custom artwork."  # Fallback
                except Exception as e:
                    ai_desc = "A unique print-on-demand product featuring custom artwork."  # Fallback

        desc = clean_ai_response(ai_desc) + custom_html
        return jsonify({'description': desc})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate_tags', methods=['POST'])
def generate_tags():
    data = request.json
    provider = data.get('provider', 'openai')
    key = data.get(f'{provider}_key')
    if not key:
        return jsonify({'error': f'{provider.capitalize()} API key required'}), 400

    try:
        max_tags = int(data.get('max_tags', 10))
        evergreen = data.get('evergreen', '').split(',')

        prompt = f"Generate {max_tags} relevant tags for a custom print-on-demand product. Make them SEO-friendly and appealing. Return as a comma-separated list."

        if provider == 'gemini':
            genai.configure(api_key=key)
            model = genai.GenerativeModel('models/gemini-2.0-flash')
            response = model.generate_content(prompt)
            ai_tags_str = response.text.strip()
        elif provider == 'openai':
            import openai
            client = openai.OpenAI(api_key=key)
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.7,
            )
            ai_tags_str = response.choices[0].message.content.strip()
        elif provider == 'ollama':
            secure_img = secure_filename(data.get('image_path', ''))
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_img)
            if os.path.exists(img_path):
                with open(img_path, 'rb') as f:
                    image_data = f.read()
                pil_image = Image.open(io.BytesIO(image_data))
                # Resize to max 1024x1024 to reduce size
                max_size = (1024, 1024)
                pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                pil_image.save(buffer, format='PNG')
                resized_data = buffer.getvalue()
                image_b64 = base64.b64encode(resized_data).decode('utf-8')
                payload = {
                    "model": data.get('ollama_model', 'llava'),
                    "prompt": prompt,
                    "images": [image_b64],
                    "stream": False
                }
                response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=120)
                if response.status_code == 200:
                    ai_tags_str = response.json().get('response', 'No response').strip()
                else:
                    ai_tags_str = "custom, print-on-demand, artwork"  # Fallback
            else:
                ai_tags_str = "custom, print-on-demand, artwork"  # Fallback

        ai_tags = [tag.strip() for tag in ai_tags_str.split(',') if tag.strip()]
        tags = ai_tags[:max_tags] + evergreen
        return jsonify({'tags': tags})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/set_keys', methods=['POST'])
def set_keys():
    data = request.json
    keys = load_keys()
    if 'printify_key' in data:
        session['printify_key'] = data['printify_key']
        keys['printify_key'] = data['printify_key']
    if 'openai_key' in data:
        session['openai_key'] = data['openai_key']
        keys['openai_key'] = data['openai_key']
    if 'gemini_key' in data:
        session['gemini_key'] = data['gemini_key']
        keys['gemini_key'] = data['gemini_key']
    save_keys(keys)
    return jsonify({'message': 'API keys saved'})

@app.route('/api/get_keys', methods=['GET'])
def get_keys():
    keys = load_keys()
    return jsonify({
        'printify_key_set': 'printify_key' in keys,
        'openai_key_set': 'openai_key' in keys,
        'gemini_key_set': 'gemini_key' in keys,
        'printify_key': keys.get('printify_key', ''),
        'openai_key': keys.get('openai_key', ''),
        'gemini_key': keys.get('gemini_key', '')
    })

@app.route('/api/product_details', methods=['GET'])
def get_product_details():
    store_id = request.args.get('store_id')
    product_id = request.args.get('product_id')
    auth_header = request.headers.get('Authorization')
    api_key = auth_header.replace('Bearer ', '') if auth_header else session.get('printify_key')
    if not api_key:
        return jsonify({'error': 'API key required'}), 401
    headers = {'Authorization': f'Bearer {api_key}'}
    try:
        response = requests.get(f'https://api.printify.com/v1/shops/{store_id}/products/{product_id}.json', headers=headers)
        if response.status_code == 200:
            product = response.json()
            return jsonify({
                'title': product.get('title', ''),
                'description': product.get('description', ''),
                'tags': product.get('tags', [])
            })
        else:
            return jsonify({'error': f'Failed to fetch product: {response.text}'}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ollama_models', methods=['GET'])
def get_ollama_models():
    url = 'http://localhost:11434/api/tags'
    try:
        response = requests.get(url)
        response.raise_for_status()
        models = response.json().get('models', [])
        model_names = [model['name'] for model in models]
        return jsonify(model_names)
    except requests.exceptions.HTTPError as e:
        error_message = f'Ollama server returned an error: {response.status_code}. Response: {response.text[:200]}'
        return jsonify({'error': error_message}), response.status_code
    except requests.exceptions.RequestException as e:
        error_message = 'Could not connect to Ollama server. Is it running at http://localhost:11434?'
        return jsonify({'error': error_message}), 500
    except json.JSONDecodeError as e:
        error_message = f'Invalid JSON response from Ollama server. Response: {response.text[:200]}'
        return jsonify({'error': error_message}), 500
    except Exception as e:
        return jsonify({'error': 'An unexpected error occurred.'}), 500

if __name__ == '__main__':
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        print("🎬 BHTools Bulk POD Product Uploader")
        print("Starting server at http://127.0.0.1:5000/")
    app.run(debug=True)
