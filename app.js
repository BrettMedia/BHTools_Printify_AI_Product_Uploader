document.addEventListener('DOMContentLoaded', function() {
    const imageFilesInput = document.getElementById('image-files');
    const fileList = document.getElementById('file-list');
    const storeSelect = document.getElementById('store-select');
    const productSelect = document.getElementById('product-select');
    const createProductsBtn = document.getElementById('create-products');
    const progressStatus = document.getElementById('status');
    const progressMessage = document.getElementById('progress-message');
    const cancelBtn = document.getElementById('cancel');


    let uploadedFiles = [];
    let progressInterval;
    let apiConnected = false;
    let validatingPrintify = false;

    // Function to load stores
    function loadStores(apiKey) {
        const statusDiv = document.getElementById('api-status');
        if (apiKey) {
            statusDiv.textContent = 'Connecting to Printify...';
            statusDiv.style.color = 'orange';
            fetch('/api/stores', {
                headers: {'Authorization': `Bearer ${apiKey}`}
            })
                .then(res => res.json())
                .then(stores => {
                    storeSelect.innerHTML = '<option value="">Select Store</option>';
                    stores.forEach(store => {
                        const option = document.createElement('option');
                        option.value = store.id;
                        option.textContent = store.name;
                        storeSelect.appendChild(option);
                    });
                    apiConnected = true;
                    statusDiv.textContent = 'Printify API connected successfully!';
                    statusDiv.style.color = 'green';
                })
                .catch(err => {
                    apiConnected = false;
                    statusDiv.textContent = 'Failed to connect to Printify API. Please check your API key.';
                    statusDiv.style.color = 'red';
                });
        } else {
            statusDiv.textContent = '';
        }
    }

    // Load stores when API key is entered
    const apiKeyInput = document.getElementById('api-key');
    apiKeyInput.addEventListener('blur', function() {
        loadStores(this.value);
    });

    // Validate AI keys based on provider
    const aiProviderSelect = document.getElementById('ai-provider');
    const ollamaOptions = document.getElementById('ollama-options');
    const ollamaModelSelect = document.getElementById('ollama-model');

    function loadOllamaModels() {
        console.log('Attempting to fetch Ollama models...');
        const ollamaStatus = document.getElementById('ollama-status');
        ollamaStatus.textContent = 'Loading Ollama models...';
        ollamaStatus.style.color = 'orange';

        fetch('/api/ollama_models')
            .then(res => res.json()) // Always parse JSON
            .then(models => {
                console.log('Successfully fetched and parsed Ollama models:', models);
                ollamaModelSelect.innerHTML = '<option value="">Select Ollama Model</option>';
                if (Array.isArray(models)) {
                    models.forEach(model => {
                        const option = document.createElement('option');
                        option.value = model;
                        option.textContent = model;
                        ollamaModelSelect.appendChild(option);
                    });
                    ollamaStatus.textContent = 'Ollama models loaded successfully!';
                    ollamaStatus.style.color = 'green';
                    if (models.length > 0) {
                        ollamaModelSelect.selectedIndex = 1; // Auto-select the first model
                    }
                } else if (models.error) {
                    const errorMsg = 'Error fetching Ollama models: ' + models.error;
                    console.error(errorMsg);
                    ollamaStatus.textContent = errorMsg;
                    ollamaStatus.style.color = 'red';
                }
            })
            .catch(err => {
                const errorMsg = 'Error fetching Ollama models: ' + err.message;
                console.error(errorMsg);
                ollamaStatus.textContent = errorMsg;
                ollamaStatus.style.color = 'red';
            });
    }

    aiProviderSelect.addEventListener('change', function() {
        const provider = this.value;
        if (provider === 'ollama') {
            ollamaOptions.style.display = 'block';
            loadOllamaModels();
        } else {
            ollamaOptions.style.display = 'none';
        }

        if (provider === 'openai') {
            validateOpenAIKey();
        } else if (provider === 'gemini') {
            validateGeminiKey();
        } else if (provider === 'ollama') {
            validateOllama();
        }
    });

    // Load products when store selected
    storeSelect.addEventListener('change', function() {
        const storeId = this.value;
        const apiKey = apiKeyInput.value;
        productSelect.innerHTML = '<option value="">Select Example Product</option>';
        if (storeId && apiKey) {
            fetch(`/api/products?store_id=${storeId}`, {
                headers: {'Authorization': `Bearer ${apiKey}`}
            })
                .then(res => res.json())
                .then(products => {
                    products.forEach(product => {
                        const option = document.createElement('option');
                        option.value = product.id;
                        option.textContent = product.title;
                        productSelect.appendChild(option);
                    });
                });
        }
    });

    // Handle file selection
    imageFilesInput.addEventListener('change', function() {
        const files = Array.from(this.files);
        fileList.innerHTML = '';
        uploadedFiles = [];
        const formData = new FormData();
        files.forEach(file => {
            formData.append('files', file);
            const div = document.createElement('div');
            div.textContent = file.name;
            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = 'Delete';
            deleteBtn.addEventListener('click', function() {
                fetch('/api/delete_file', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({filename: file.name})
                })
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        div.remove();
                        uploadedFiles = uploadedFiles.filter(f => f !== file.name);
                    } else {
                        alert('Error deleting file: ' + data.error);
                    }
                });
            });
            div.appendChild(deleteBtn);
            fileList.appendChild(div);
            uploadedFiles.push(file.name);
        });

        // Upload files to backend
        fetch('/api/upload', {
            method: 'POST',
            body: formData
        }).then(res => res.json()).then(data => {
            console.log('Uploaded:', data.uploaded);
        });
    });

    // Show/hide AI title options
    document.querySelectorAll('input[name="title-source"]').forEach(radio => {
        radio.addEventListener('change', function() {
            document.getElementById('ai-title-options').style.display = this.value === 'ai' ? 'block' : 'none';
        });
    });

    // Create products
    createProductsBtn.addEventListener('click', function() {
        if (uploadedFiles.length === 0) {
            alert('Please select at least one image file.');
            return;
        }
        if (!storeSelect.value) {
            alert('Please select a store.');
            return;
        }
        if (!productSelect.value) {
            alert('Please select an example product.');
            return;
        }

        if (!confirm('Are you sure you want to create ' + uploadedFiles.length + ' products?')) {
            return;
        }

        const provider = document.getElementById('ai-provider').value;
        const data = {
            images: uploadedFiles,
            placement_mode: document.querySelector('input[name="placement"]:checked').value,
            store_id: storeSelect.value,
            product_id: productSelect.value,
            api_key: apiKeyInput.value,
            openai_key: provider === 'openai' ? document.getElementById('openai-key').value : '',
            gemini_key: provider === 'gemini' ? document.getElementById('gemini-key').value : '',
            rules: {
                ai_provider: provider,
                ollama_model: document.getElementById('ollama-model').value,
                title_source: document.querySelector('input[name="title-source"]:checked').value,
                ai_title_mode: document.querySelector('input[name="ai-title-mode"]:checked')?.value,
                compound_segments: document.getElementById('compound-segments').value,
                custom_title_text: document.getElementById('custom-title-text').value,
                title_template: document.getElementById('title-template').value,
                desc_source: document.querySelector('input[name="desc-source"]:checked').value,
                desc_paragraphs: document.getElementById('desc-paragraphs').value,
                influencer_phrases: document.getElementById('influencer-phrases').value,
                custom_html: document.getElementById('custom-html').value,
                tag_source: document.querySelector('input[name="tag-source"]:checked').value,
                max_ai_tags: document.getElementById('max-ai-tags').value,
                evergreen_tags: document.getElementById('evergreen-tags').value
            }
        };

        fetch('/api/create_products', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(data)
        }).then(res => res.json()).then(() => {
            // Start polling progress
            progressInterval = setInterval(updateProgress, 1000);
        });
    });

    function updateProgress() {
        fetch('/api/progress')
            .then(res => res.json())
            .then(data => {
                progressStatus.textContent = `Status: ${data.status}`;
                document.getElementById('progress-text').textContent = `Progress: ${data.current}/${data.total}`;
                progressMessage.textContent = data.message;
                if (data.status === 'completed' || data.status === 'cancelled' || data.status === 'error') {
                    clearInterval(progressInterval);
                    cancelBtn.disabled = true;
                }
            });
    }

    // Cancel
    cancelBtn.addEventListener('click', function() {
        fetch('/api/cancel', {
            method: 'POST'
        }).then(res => res.json()).then(() => {
            clearInterval(progressInterval);
            progressStatus.textContent = 'Status: Cancelled';
            cancelBtn.disabled = true;
        });
    });



    // Collapse/expand sections
    document.querySelectorAll('.collapse-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const targetId = this.getAttribute('data-target');
            const target = document.getElementById(targetId);
            const computedDisplay = window.getComputedStyle(target).display;
            if (computedDisplay === 'none') {
                target.style.display = 'block';
                this.textContent = '-';
            } else {
                target.style.display = 'none';
                this.textContent = '+';
            }
        });
    });

    // Modal functionality
    const helpToggle = document.getElementById('help-toggle');
    const helpModal = document.getElementById('help-modal');
    const closeHelp = document.getElementById('close-help');

    helpToggle.addEventListener('click', function(event) {
        event.preventDefault();
        helpModal.style.display = 'block';
    });

    closeHelp.addEventListener('click', function() {
        helpModal.style.display = 'none';
    });

    window.addEventListener('click', function(event) {
        if (event.target === helpModal) {
            helpModal.style.display = 'none';
        }
    });

    // Add event listeners for API key validation
    document.getElementById('api-key').addEventListener('blur', validatePrintifyKey);
    document.getElementById('openai-key').addEventListener('blur', validateOpenAIKey);
    document.getElementById('gemini-key').addEventListener('blur', validateGeminiKey);



});

// API key validation functions
function validatePrintifyKey() {
    if (validatingPrintify) return;
    const apiKey = document.getElementById('api-key').value;
    if (!apiKey) return;
    validatingPrintify = true;
    fetch('/api/stores', {
        headers: {'Authorization': `Bearer ${apiKey}`}
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            const statusDiv = document.getElementById('api-status');
            statusDiv.textContent = 'Invalid Printify API Key: ' + data.error;
            statusDiv.style.color = 'red';
        } else {
            const statusDiv = document.getElementById('api-status');
            statusDiv.textContent = 'Printify API Key validated successfully!';
            statusDiv.style.color = 'green';
        }
        validatingPrintify = false;
    })
    .catch(err => {
        const statusDiv = document.getElementById('api-status');
        statusDiv.textContent = 'Error validating Printify API Key: ' + err.message;
        statusDiv.style.color = 'red';
        validatingPrintify = false;
    });
}

function validateOpenAIKey() {
    const openaiKey = document.getElementById('openai-key').value;
    const statusDiv = document.getElementById('openai-status');
    if (!openaiKey) {
        statusDiv.textContent = '';
        return;
    }
    statusDiv.textContent = 'Validating OpenAI API Key...';
    statusDiv.style.color = 'orange';
    fetch('/api/generate_title', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({provider: 'openai', openai_key: openaiKey, mode: 'simple'})
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            statusDiv.textContent = 'Invalid OpenAI API Key: ' + data.error;
            statusDiv.style.color = 'red';
        } else {
            statusDiv.textContent = 'OpenAI API Key validated successfully!';
            statusDiv.style.color = 'green';
        }
    })
    .catch(err => {
        statusDiv.textContent = 'Error validating OpenAI API Key: ' + err.message;
        statusDiv.style.color = 'red';
    });
}

function validateGeminiKey() {
    const geminiKey = document.getElementById('gemini-key').value;
    const statusDiv = document.getElementById('gemini-status');
    if (!geminiKey) {
        statusDiv.textContent = '';
        return;
    }
    statusDiv.textContent = 'Validating Gemini API Key...';
    statusDiv.style.color = 'orange';
    fetch('/api/generate_title', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({provider: 'gemini', gemini_key: geminiKey, mode: 'simple'})
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            statusDiv.textContent = 'Invalid Gemini API Key: ' + data.error;
            statusDiv.style.color = 'red';
        } else {
            statusDiv.textContent = 'Gemini API Key validated successfully!';
            statusDiv.style.color = 'green';
        }
    })
    .catch(err => {
        statusDiv.textContent = 'Error validating Gemini API Key: ' + err.message;
        statusDiv.style.color = 'red';
    });
}

function validateOllama() {
    const statusDiv = document.getElementById('api-status'); // Use general status since no specific Ollama status div
    statusDiv.textContent = 'Checking Ollama connection...';
    statusDiv.style.color = 'orange';
    fetch('/api/generate_title', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({provider: 'ollama', mode: 'simple'})
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            statusDiv.textContent = 'Ollama not available: ' + data.error;
            statusDiv.style.color = 'red';
        } else {
            statusDiv.textContent = 'Ollama connected successfully!';
            statusDiv.style.color = 'green';
        }
    })
    .catch(err => {
        statusDiv.textContent = 'Error checking Ollama: ' + err.message;
        statusDiv.style.color = 'red';
    });
}