<?php
// File: includes/class-chatbot-rag.php

if ( ! defined( 'ABSPATH' ) ) exit;

if ( ! class_exists( 'Chatbot_RAG' ) ) :

class Chatbot_RAG {

    private $python_script_path;
    public  $vector_db_path;
    private $plugin_path;
    private $python_path;
    private $openai_api_key;

    public function __construct() {
        $this->plugin_path        = COC_PLUGIN_DIR;
        $this->python_script_path = $this->plugin_path . 'rag_processor/rag_processor.py';
        $this->vector_db_path     = $this->plugin_path . 'rag_processor/vector_db';
        $this->openai_api_key     = get_option( 'coc_openai_api_key', '' );

        // Auto-detect Python — works across machines
        $python_candidates = [
            '/Users/mrtlpa/miniconda3/envs/rag_env/bin/python3',
            '/Users/mrtlpa/miniconda3/envs/rag_env/bin/python',
            '/opt/anaconda3/envs/rag_env/bin/python3',
            '/opt/anaconda3/envs/rag_env/bin/python',
            '/opt/homebrew/bin/python3',
            '/usr/local/bin/python3',
            '/usr/bin/python3',
        ];
        $this->python_path = 'python3';
        foreach ( $python_candidates as $candidate ) {
            if ( file_exists( $candidate ) ) {
                $this->python_path = $candidate;
                break;
            }
        }

        wp_mkdir_p( $this->vector_db_path );
        wp_mkdir_p( $this->plugin_path . 'documents' );

        // AJAX handlers
        $ajax_actions = [
            'coc_rag_query'       => [ 'handle_rag_query',       true  ],
            'coc_list_documents'  => [ 'handle_list_documents',  false ],
            'coc_process_pdf'     => [ 'handle_process_pdf',     false ],
            'coc_process_website' => [ 'handle_process_website', false ],
            'coc_website_query'   => [ 'handle_website_query',   true  ],
            'coc_list_websites'   => [ 'handle_list_websites',   false ],
            'coc_clear_all'       => [ 'handle_clear_all',       false ],
            'coc_remove_document' => [ 'handle_remove_document', false ],
            'coc_remove_website'  => [ 'handle_remove_website',  false ],
            'coc_rag_status'      => [ 'handle_rag_status',      false ],
            'coc_save_openai_key' => [ 'handle_save_openai_key', false ],
        ];

        foreach ( $ajax_actions as $action => [ $method, $nopriv ] ) {
            add_action( "wp_ajax_{$action}", [ $this, $method ] );
            if ( $nopriv ) {
                add_action( "wp_ajax_nopriv_{$action}", [ $this, $method ] );
            }
        }

        add_action( 'admin_menu', [ $this, 'add_rag_admin_menu' ] );
    }

    // =========================================================================
    // Admin Menu
    // =========================================================================

    public function add_rag_admin_menu() {
        add_submenu_page(
            'options-general.php',
            'RAG Document Manager',
            'RAG Documents',
            'manage_options',
            'rag-documents',
            [ $this, 'render_rag_admin_page' ]
        );
    }

    public function render_rag_admin_page() {
        ?>
        <div class="wrap">
            <h1>🤖 RAG Knowledge Base Manager</h1>

            <h2 class="nav-tab-wrapper">
                <a href="#pdf-tab"     class="nav-tab nav-tab-active" data-tab="pdf-tab">📄 PDF Documents</a>
                <a href="#website-tab" class="nav-tab"                data-tab="website-tab">🌐 Website Training</a>
                <a href="#status-tab"  class="nav-tab"                data-tab="status-tab">🔧 System Status</a>
            </h2>

            <div id="pdf-tab"     class="tab-content"><?php $this->render_pdf_tab(); ?></div>
            <div id="website-tab" class="tab-content" style="display:none;"><?php $this->render_website_tab(); ?></div>
            <div id="status-tab"  class="tab-content" style="display:none;"><?php $this->render_status_tab(); ?></div>
        </div>

        <style>
        .tab-content       { padding: 20px 0; }
        .nav-tab-active    { background: white; border-bottom-color: transparent; }
        .rag-progress      { margin-top:15px; padding:15px; background:#f8f9fa; border-left:4px solid #4361ee; border-radius:4px; }
        .rag-success       { background:#d4edda; border-left-color:#28a745; }
        .rag-error         { background:#f8d7da; border-left-color:#dc3545; }
        .clear-all-btn     { background:#dc3545!important; color:white!important; border-color:#dc3545!important; }
        .status-processed  { background:#d4edda; color:#155724; padding:3px 8px; border-radius:3px; font-size:12px; }
        .status-pending    { background:#fff3cd; color:#856404; padding:3px 8px; border-radius:3px; font-size:12px; }
        pre.rag-log        { background:#1e1e1e; color:#d4d4d4; padding:12px; border-radius:4px; max-height:220px; overflow:auto; font-size:12px; }
        </style>

        <script>
        jQuery(document).ready(function($){

            // Tab switching
            $('.nav-tab').on('click', function(e){
                e.preventDefault();
                $('.nav-tab').removeClass('nav-tab-active');
                $(this).addClass('nav-tab-active');
                $('.tab-content').hide();
                $('#' + $(this).data('tab')).show();
            });

            // PDF Upload
            $('#pdf-upload-form').on('submit', function(e){
                e.preventDefault();
                var fd = new FormData(this);
                fd.append('action', 'coc_process_pdf');
                fd.append('nonce', '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>');
                $('#pdf-progress').show().removeClass('rag-success rag-error');
                $('#pdf-log').empty();
                $('#pdf-msg').html('<span class="spinner is-active" style="float:none"></span> Uploading &amp; processing PDF...');
                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: fd, processData: false, contentType: false,
                    timeout: 300000,
                    success: function(r){
                        $('#pdf-progress').addClass(r.success ? 'rag-success' : 'rag-error');
                        $('#pdf-msg').html(r.success ? '✅ PDF processed successfully!' : '❌ Error: ' + r.data);
                        if (r.success && r.data.output) $('#pdf-log').text(r.data.output).show();
                        if (r.success) { $('#rag_document').val(''); loadDocsList(); }
                    },
                    error: function(xhr, status){
                        $('#pdf-progress').addClass('rag-error');
                        $('#pdf-msg').html('❌ Request failed: ' + status);
                    }
                });
            });

            // Process Website
            $('#process-website-btn').on('click', function(){
                var url      = $.trim($('#website_url').val());
                var maxPages = parseInt($('#max_pages').val()) || 20;
                if (!url) { alert('Please enter a website URL'); return; }
                try { new URL(url); } catch(e) { alert('Please enter a valid URL including https://'); return; }

                $(this).prop('disabled', true).text('⏳ Processing...');
                $('#website-progress').show().removeClass('rag-success rag-error');
                $('#website-log').empty();
                $('#website-msg').html('<span class="spinner is-active" style="float:none"></span> Crawling &amp; processing website — this may take a few minutes...');

                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: { action: 'coc_process_website', url: url, max_pages: maxPages,
                            nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    timeout: 600000,
                    success: function(r){
                        $('#process-website-btn').prop('disabled', false).text('🌐 Train on Website');
                        $('#website-progress').addClass(r.success ? 'rag-success' : 'rag-error');
                        if (r.success) {
                            $('#website-msg').html('✅ Website processed successfully!');
                            $('#website-log').text(
                                'Pages processed : ' + r.data.pages      + '\n' +
                                'Chunks created  : ' + r.data.chunks     + '\n' +
                                'Collection      : ' + r.data.collection_name
                            );
                            $('#website_url').val('');
                            loadWebsitesList();
                        } else {
                            $('#website-msg').html('❌ Error: ' + r.data);
                        }
                    },
                    error: function(xhr, status){
                        $('#process-website-btn').prop('disabled', false).text('🌐 Train on Website');
                        $('#website-progress').addClass('rag-error');
                        $('#website-msg').html('❌ Request failed (' + status + '). Server may still be processing — check back shortly.');
                    }
                });
            });

            // Test Query
            $('#test-query-btn').on('click', function(){
                var q = $.trim($('#test_query_input').val());
                if (!q) { alert('Please enter a question'); return; }
                $(this).prop('disabled', true).text('🤔 Thinking...');
                $('#query-result').show();

                var elapsed = 0;
                var timer = setInterval(function(){
                    elapsed++;
                    $('#query-answer').html(
                        '<div class="spinner is-active" style="float:none;margin-right:8px;"></div>'
                        + '<span style="color:#666">Asking OpenAI... ' + elapsed + 's</span>'
                        + (elapsed > 10 ? '<br><small style="color:#999">(Large responses may take a moment)</small>' : '')
                    );
                }, 1000);

                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: { action: 'coc_rag_query', question: q,
                            nonce: '<?php echo wp_create_nonce( 'coc_chat_nonce' ); ?>' },
                    timeout: 90000,
                    success: function(r){
                        clearInterval(timer);
                        $('#test-query-btn').prop('disabled', false).text('🔍 Ask');
                        $('#query-answer').html(r.success
                            ? '<div style="white-space:pre-wrap">' + $('<div>').text(r.data.answer).html() + '</div>'
                            : '<span style="color:red">Error: ' + r.data + '</span>');
                    },
                    error: function(xhr, status){
                        clearInterval(timer);
                        $('#test-query-btn').prop('disabled', false).text('🔍 Ask');
                        $('#query-answer').html('<span style="color:red">Request failed: ' + status + '</span>');
                    }
                });
            });
            $('#test_query_input').on('keypress', function(e){
                if (e.which === 13) { e.preventDefault(); $('#test-query-btn').click(); }
            });

            // Status Check
            $('#check-status-btn').on('click', function(){
                $(this).prop('disabled', true).text('Checking...');
                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: { action: 'coc_rag_status',
                            nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    success: function(r){
                        $('#check-status-btn').prop('disabled', false).text('🔄 Refresh Status');
                        if (!r.success) return;
                        var s = r.data.status, html = '';
                        html += s.openai_key_set
                            ? '<p>✅ <strong>OpenAI API Key</strong> – configured</p>'
                            : '<p>❌ <strong>OpenAI API Key</strong> – not set (go to System Status tab to add it)</p>';
                        html += s.python_exists
                            ? '<p>✅ <strong>Python</strong> – found</p>'
                            : '<p>❌ <strong>Python</strong> – not found at expected path</p>';
                        html += s.rag_script_exists
                            ? '<p>✅ <strong>RAG script</strong> – found</p>'
                            : '<p>❌ <strong>RAG script</strong> – missing (check plugin files)</p>';
                        html += s.vector_db
                            ? '<p>✅ <strong>Knowledge Base</strong> – has data</p>'
                            : '<p>⚠️ <strong>Knowledge Base</strong> – empty (upload a PDF or process a website)</p>';
                        html += '<p><strong>Total chunks processed:</strong> ' + (r.data.total_chunks || 0) + '</p>';
                        $('#status-result').html(html);
                    },
                    error: function(){
                        $('#check-status-btn').prop('disabled', false).text('🔄 Refresh Status');
                    }
                });
            });

            // Clear All
            $(document).on('click', '.clear-all-btn', function(){
                if (!confirm('Delete ALL documents and websites? This cannot be undone.')) return;
                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: { action: 'coc_clear_all',
                            nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    success: function(r){
                        if (r.success) { alert('All data cleared.'); location.reload(); }
                        else alert('Error: ' + r.data);
                    }
                });
            });

            // Remove Document
            $(document).on('click', '.remove-document', function(){
                var f = $(this).data('file');
                if (!confirm('Delete ' + f + '?')) return;
                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: { action: 'coc_remove_document', filename: f,
                            nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    success: function(r){ if (r.success) loadDocsList(); else alert('Error: ' + r.data); }
                });
            });

            // Remove Website
            $(document).on('click', '.remove-website', function(){
                var c = $(this).data('collection');
                if (!confirm('Remove this website from the knowledge base?')) return;
                $.ajax({
                    url: ajaxurl, type: 'POST',
                    data: { action: 'coc_remove_website', collection: c,
                            nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    success: function(r){ if (r.success) loadWebsitesList(); else alert('Error: ' + r.data); }
                });
            });

            // Helpers
            function loadDocsList(){
                $.post(ajaxurl,
                    { action: 'coc_list_documents', nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    function(r){ if (r.success) $('#documents-list').html(r.data.html); }
                );
            }
            function loadWebsitesList(){
                $.post(ajaxurl,
                    { action: 'coc_list_websites', nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>' },
                    function(r){ if (r.success) $('#websites-list').html(r.data.html); }
                );
            }

            // Auto-run status on page load
            $('#check-status-btn').trigger('click');
        });
        </script>
        <?php
    }

    // =========================================================================
    // Tab rendering
    // =========================================================================

    private function render_pdf_tab() { ?>
        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;">
            <h2>📄 Upload PDF Document</h2>
            <p>Upload company policy documents, employee data, service manuals, etc.</p>
            <form id="pdf-upload-form" enctype="multipart/form-data">
                <input type="file" name="rag_document" id="rag_document" accept=".pdf" required>
                <input type="submit" class="button button-primary" value="Upload &amp; Process PDF">
            </form>
            <div id="pdf-progress" class="rag-progress" style="display:none;margin-top:15px;">
                <div id="pdf-msg"></div>
                <pre id="pdf-log" class="rag-log" style="margin-top:10px;display:none;"></pre>
            </div>
        </div>

        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;">
            <h2>📋 Processed Documents</h2>
            <div id="documents-list"><?php $this->show_processed_documents(); ?></div>
        </div>

        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;">
            <h2>🔍 Test Query</h2>
            <div style="display:flex;gap:8px;align-items:center;">
                <input type="text" id="test_query_input"
                       placeholder="e.g. What property management services do you offer?"
                       style="width:420px;">
                <button id="test-query-btn" class="button button-primary">🔍 Ask</button>
            </div>
            <div id="query-result" style="display:none;margin-top:15px;background:#f0f6fc;padding:15px;border-radius:5px;">
                <strong>Answer:</strong>
                <div id="query-answer" style="margin-top:8px;"></div>
            </div>
        </div>

        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;background:#fff3cd;">
            <h2>⚠️ Danger Zone</h2>
            <button class="button clear-all-btn">🗑️ Delete All Documents &amp; Websites</button>
            <p class="description">Permanently removes all PDFs and processed websites from the knowledge base.</p>
        </div>
    <?php }

    private function render_website_tab() { ?>
        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;">
            <h2>🌐 Train from Website</h2>
            <p>Enter a website URL. The system will crawl and process its content into the knowledge base.</p>
            <table class="form-table">
                <tr>
                    <th>Website URL</th>
                    <td>
                        <input type="url" id="website_url" placeholder="https://example.com" style="width:420px;">
                        <p class="description">Include https://</p>
                    </td>
                </tr>
                <tr>
                    <th>Max Pages</th>
                    <td>
                        <input type="number" id="max_pages" value="20" min="1" max="100" style="width:80px;">
                        <p class="description">Recommended: 10–20 for most sites. Use 50–100 for large sites.</p>
                    </td>
                </tr>
            </table>
            <button id="process-website-btn" class="button button-primary">🌐 Train on Website</button>
            <div id="website-progress" class="rag-progress" style="display:none;margin-top:15px;">
                <div id="website-msg"></div>
                <pre id="website-log" class="rag-log" style="margin-top:10px;"></pre>
            </div>
        </div>

        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;">
            <h2>📚 Processed Websites</h2>
            <div id="websites-list"><?php $this->list_processed_websites(); ?></div>
        </div>

        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;background:#fff3cd;">
            <h2>⚠️ Danger Zone</h2>
            <button class="button clear-all-btn">🗑️ Delete All Documents &amp; Websites</button>
        </div>
    <?php }

    private function render_status_tab() {
        $saved_key   = get_option( 'coc_openai_api_key', '' );
        $key_display = $saved_key ? substr( $saved_key, 0, 7 ) . '...' . substr( $saved_key, -4 ) : '';
        ?>
        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;border-left:4px solid #4361ee;">
            <h2>🔑 OpenAI API Key</h2>
            <p>
                Powers the chatbot with GPT-4o — accurate, fast, complete responses.<br>
                Get your key at <a href="https://platform.openai.com/api-keys" target="_blank">platform.openai.com/api-keys</a>.
            </p>
            <table class="form-table">
                <tr>
                    <th><label for="coc_openai_key_input">API Key</label></th>
                    <td>
                        <input type="password" id="coc_openai_key_input" class="regular-text"
                               placeholder="sk-..." value="<?php echo esc_attr( $key_display ); ?>">
                        <button id="save-openai-key-btn" class="button button-primary" style="margin-left:8px;">
                            💾 Save Key
                        </button>
                        <span id="key-save-msg" style="margin-left:10px;"></span>
                        <?php if ( $saved_key ) : ?>
                        <br><span style="color:#28a745;font-size:12px;">✅ Key saved — using OpenAI GPT-4o</span>
                        <?php else : ?>
                        <br><span style="color:#dc3545;font-size:12px;">❌ No API key set — chatbot will not respond until you add one</span>
                        <?php endif; ?>
                    </td>
                </tr>
            </table>
        </div>

        <div class="card" style="max-width:100%;padding:20px;margin-top:20px;">
            <h2>🔧 System Status</h2>
            <button id="check-status-btn" class="button">🔄 Refresh Status</button>
            <div id="status-result" style="margin-top:15px;"></div>
        </div>

        <script>
        jQuery(document).ready(function($){
            $('#save-openai-key-btn').on('click', function(){
                var key = $('#coc_openai_key_input').val().trim();
                if (!key || key.indexOf('...') !== -1) {
                    alert('Please paste your full API key (starting with sk-)');
                    return;
                }
                $(this).prop('disabled', true).text('Saving...');
                $.post(ajaxurl, {
                    action: 'coc_save_openai_key', key: key,
                    nonce: '<?php echo wp_create_nonce( 'coc_website_nonce' ); ?>'
                }, function(r){
                    $('#save-openai-key-btn').prop('disabled', false).text('💾 Save Key');
                    if (r.success) {
                        $('#key-save-msg').html('<span style="color:#28a745">✅ Saved! Reloading...</span>');
                        setTimeout(function(){ location.reload(); }, 1200);
                    } else {
                        $('#key-save-msg').html('<span style="color:red">❌ ' + r.data + '</span>');
                    }
                });
            });
        });
        </script>
    <?php }

    // =========================================================================
    // AJAX Handlers
    // =========================================================================

    public function handle_save_openai_key() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        if ( ! current_user_can( 'manage_options' ) ) wp_send_json_error( 'Unauthorized' );
        $key = sanitize_text_field( $_POST['key'] ?? '' );
        if ( empty( $key ) || strpos( $key, 'sk-' ) !== 0 ) {
            wp_send_json_error( 'Invalid API key — must start with sk-' );
        }
        update_option( 'coc_openai_api_key', $key );
        $this->openai_api_key = $key;
        wp_send_json_success( 'Key saved' );
    }

    public function handle_rag_query() {
        check_ajax_referer( 'coc_chat_nonce', 'nonce' );

        $question = sanitize_text_field( $_POST['question'] ?? '' );
        if ( empty( $question ) ) wp_send_json_error( 'Question is empty' );

        $context = sanitize_textarea_field( $_POST['context'] ?? '' );

        if ( ! file_exists( $this->vector_db_path . '/chroma.sqlite3' ) ) {
            wp_send_json_error( 'No knowledge base found. Please upload a PDF or process a website first.' );
        }

        $key_arg     = ! empty( $this->openai_api_key )
            ? ' --openai-key ' . escapeshellarg( $this->openai_api_key ) : '';
        $context_arg = ! empty( $context )
            ? ' --context ' . escapeshellarg( $context ) : '';

        $command = sprintf(
            'cd %s && %s %s --query %s%s%s 2>&1',
            escapeshellarg( $this->plugin_path ),
            escapeshellarg( $this->python_path ),
            escapeshellarg( $this->python_script_path ),
            escapeshellarg( $question ),
            $key_arg,
            $context_arg
        );

        @set_time_limit( 90 );
        ini_set( 'max_execution_time', '90' );
        $output = shell_exec( $command );

        if ( $output ) {
            wp_send_json_success( [ 'answer' => $this->extract_answer_from_output( $output ) ] );
        } else {
            wp_send_json_error( 'No response from AI. Please check your OpenAI API key in System Status.' );
        }
    }

    public function handle_list_documents() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        ob_start();
        $this->show_processed_documents();
        wp_send_json_success( [ 'html' => ob_get_clean() ] );
    }

    public function handle_process_pdf() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );

        $doc_dir = $this->plugin_path . 'documents/';
        wp_mkdir_p( $doc_dir );

        if ( ! empty( $_FILES['rag_document'] ) ) {
            $file = $_FILES['rag_document'];
            if ( $file['error'] !== UPLOAD_ERR_OK ) wp_send_json_error( 'Upload error: ' . $file['error'] );
            $filename  = sanitize_file_name( basename( $file['name'] ) );
            $dest_path = $doc_dir . $filename;
            if ( ! move_uploaded_file( $file['tmp_name'], $dest_path ) ) {
                wp_send_json_error( 'Failed to save uploaded file' );
            }
        } elseif ( ! empty( $_POST['filename'] ) ) {
            $filename  = sanitize_file_name( $_POST['filename'] );
            $dest_path = $doc_dir . $filename;
            if ( ! file_exists( $dest_path ) ) wp_send_json_error( 'File not found: ' . $filename );
        } else {
            wp_send_json_error( 'No file specified' );
        }

        $key_arg = ! empty( $this->openai_api_key )
            ? ' --openai-key ' . escapeshellarg( $this->openai_api_key ) : '';

        $command = sprintf(
            'cd %s && %s %s --pdf %s%s 2>&1',
            escapeshellarg( $this->plugin_path ),
            escapeshellarg( $this->python_path ),
            escapeshellarg( $this->python_script_path ),
            escapeshellarg( $dest_path ),
            $key_arg
        );

        @set_time_limit( 300 );
        ini_set( 'max_execution_time', '300' );
        $output = shell_exec( $command );

        if ( $output ) {
            file_put_contents(
                $this->vector_db_path . '/pdf_' . sanitize_title( basename( $dest_path ) ) . '.json',
                json_encode( [
                    'filename'       => basename( $dest_path ),
                    'processed_date' => current_time( 'mysql' ),
                    'status'         => 'processed',
                ], JSON_PRETTY_PRINT )
            );
            wp_send_json_success( [ 'output' => $output, 'filename' => basename( $dest_path ) ] );
        } else {
            wp_send_json_error( 'Python script produced no output. Check server error log.' );
        }
    }

    public function handle_process_website() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );

        $url       = esc_url_raw( $_POST['url'] ?? '' );
        $max_pages = max( 1, min( 100, intval( $_POST['max_pages'] ?? 20 ) ) );

        if ( empty( $url ) ) wp_send_json_error( 'URL is required' );

        $command = sprintf(
            'cd %s && %s %s --website %s --max-pages %d 2>&1',
            escapeshellarg( $this->plugin_path ),
            escapeshellarg( $this->python_path ),
            escapeshellarg( $this->python_script_path ),
            escapeshellarg( $url ),
            $max_pages
        );

        error_log( 'RAG Website Command: ' . $command );

        @set_time_limit( 600 );
        ini_set( 'max_execution_time', '600' );
        $output = shell_exec( $command );

        if ( $output ) {
            preg_match( '/Collection name\s*:\s*(\S+)/i', $output, $cm );
            preg_match( '/Total chunks\s*:\s*(\d+)/i',    $output, $tc );
            preg_match( '/Pages read\s*:\s*(\d+)/i',      $output, $pm );
            wp_send_json_success( [
                'output'          => $output,
                'collection_name' => $cm[1] ?? 'website_' . md5( $url ),
                'chunks'          => intval( $tc[1] ?? 0 ),
                'pages'           => intval( $pm[1] ?? 0 ),
            ] );
        } else {
            wp_send_json_error( 'Python script produced no output. Check server error log.' );
        }
    }

    public function handle_website_query() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );

        $query = sanitize_text_field( $_POST['query'] ?? '' );
        if ( empty( $query ) ) wp_send_json_error( 'Query is required' );

        $key_arg = ! empty( $this->openai_api_key )
            ? ' --openai-key ' . escapeshellarg( $this->openai_api_key ) : '';

        $command = sprintf(
            'cd %s && %s %s --query %s%s 2>&1',
            escapeshellarg( $this->plugin_path ),
            escapeshellarg( $this->python_path ),
            escapeshellarg( $this->python_script_path ),
            escapeshellarg( $query ),
            $key_arg
        );

        @set_time_limit( 90 );
        ini_set( 'max_execution_time', '90' );
        $output = shell_exec( $command );

        if ( $output ) {
            wp_send_json_success( [ 'answer' => $this->extract_answer_from_output( $output ) ] );
        } else {
            wp_send_json_error( 'No response from AI. Please check your OpenAI API key.' );
        }
    }

    public function handle_list_websites() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        ob_start();
        $this->list_processed_websites();
        wp_send_json_success( [ 'html' => ob_get_clean() ] );
    }

    public function handle_clear_all() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        foreach ( glob( $this->vector_db_path . '/*' ) ?: [] as $f ) {
            if ( is_file( $f ) ) unlink( $f );
        }
        foreach ( glob( $this->plugin_path . 'documents/*.pdf' ) ?: [] as $f ) {
            unlink( $f );
        }
        wp_send_json_success( 'All data cleared' );
    }

    public function handle_remove_document() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        $filename = sanitize_file_name( $_POST['filename'] ?? '' );
        $path     = $this->plugin_path . 'documents/' . $filename;
        if ( file_exists( $path ) ) unlink( $path );
        $meta = $this->vector_db_path . '/pdf_' . sanitize_title( $filename ) . '.json';
        if ( file_exists( $meta ) ) unlink( $meta );
        wp_send_json_success( 'Document removed' );
    }

    public function handle_remove_website() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        $collection = sanitize_text_field( $_POST['collection'] ?? '' );
        foreach ( glob( $this->vector_db_path . '/*_metadata.json' ) ?: [] as $mf ) {
            $data = json_decode( file_get_contents( $mf ), true );
            if ( ( $data['collection_name'] ?? '' ) === $collection ) {
                unlink( $mf );
                break;
            }
        }
        wp_send_json_success( 'Website removed' );
    }

    public function handle_rag_status() {
        check_ajax_referer( 'coc_website_nonce', 'nonce' );
        $status       = $this->check_status();
        $total_chunks = 0;

        if ( $status['vector_db'] ) {
            $command = sprintf(
                'cd %s && %s %s --stats 2>&1',
                escapeshellarg( $this->plugin_path ),
                escapeshellarg( $this->python_path ),
                escapeshellarg( $this->python_script_path )
            );
            $output = shell_exec( $command );
            if ( preg_match( '/Total chunks:\s*(\d+)/i', $output, $m ) ) {
                $total_chunks = (int) $m[1];
            }
        }

        wp_send_json_success( [ 'status' => $status, 'total_chunks' => $total_chunks ] );
    }

    // =========================================================================
    // Helpers
    // =========================================================================

    private function extract_answer_from_output( $output ) {
        // Primary: extract between our clear delimiters (handles multi-paragraph GPT-4o responses)
        if ( preg_match( '/<<<ANSWER_START>>>\s*(.+?)\s*<<<ANSWER_END>>>/s', $output, $m ) ) {
            return trim( $m[1] );
        }
        // Legacy fallback: 💬 Answer: marker — capture EVERYTHING after it to end of string
        if ( preg_match( '/💬 Answer:\s*(.+)/s', $output, $m ) ) {
            return trim( $m[1] );
        }
        // Last resort: strip emoji progress lines, return remaining text
        $lines = array_filter(
            explode( "\n", $output ),
            fn( $l ) => ! preg_match( '/^\s*[📄🔧✅📊🚀💬❌⚠️🌐📚🔍INFO|OK:|={5}]/', trim( $l ) )
                     && strlen( trim( $l ) ) > 2
        );
        return trim( implode( "\n", $lines ) ) ?: $output;
    }

    private function show_processed_documents() {
        $doc_dir = $this->plugin_path . 'documents/';
        if ( ! file_exists( $doc_dir ) || empty( glob( $doc_dir . '*.pdf' ) ) ) {
            echo '<p>No PDF documents found. Upload one above!</p>';
            return;
        }
        echo '<table class="wp-list-table widefat fixed striped">';
        echo '<thead><tr><th>Document</th><th>Size</th><th>Status</th><th>Actions</th></tr></thead><tbody>';
        foreach ( glob( $doc_dir . '*.pdf' ) as $file ) {
            $fn     = basename( $file );
            $size   = size_format( filesize( $file ) );
            $done   = file_exists( $this->vector_db_path . '/pdf_' . sanitize_title( $fn ) . '.json' );
            echo '<tr>';
            echo '<td>' . esc_html( $fn ) . '</td>';
            echo '<td>' . esc_html( $size ) . '</td>';
            echo '<td><span class="document-status ' . ( $done ? 'status-processed' : 'status-pending' ) . '">'
               . ( $done ? '✅ Processed' : '⏳ Pending' ) . '</span></td>';
            echo '<td>';
            if ( ! $done ) echo '<button class="button button-small process-pdf" data-file="' . esc_attr( $fn ) . '">Process</button> ';
            echo '<button class="button button-small remove-document" data-file="' . esc_attr( $fn ) . '" '
               . 'style="background:#dc3545;color:white;border-color:#dc3545">Delete</button>';
            echo '</td></tr>';
        }
        echo '</tbody></table>';
    }

    private function list_processed_websites() {
        $files = array_filter(
            glob( $this->vector_db_path . '/*_metadata.json' ) ?: [],
            fn( $f ) => strpos( basename( $f ), 'pdf_' ) === false
        );

        if ( empty( $files ) ) {
            echo '<p>No websites processed yet. Add a URL above!</p>';
            return;
        }

        echo '<table class="wp-list-table widefat fixed striped">';
        echo '<thead><tr><th>Website URL</th><th>Pages</th><th>Chunks</th><th>Processed</th><th>Actions</th></tr></thead><tbody>';
        foreach ( $files as $mf ) {
            $data = json_decode( file_get_contents( $mf ), true );
            if ( ! isset( $data['website_url'] ) ) continue;
            echo '<tr>';
            echo '<td><a href="' . esc_url( $data['website_url'] ) . '" target="_blank">' . esc_html( $data['website_url'] ) . '</a></td>';
            echo '<td>' . esc_html( $data['pages_processed'] ?? 'N/A' ) . '</td>';
            echo '<td>' . esc_html( $data['chunk_count']     ?? 'N/A' ) . '</td>';
            echo '<td>' . esc_html( $data['processed_date']  ?? '' )    . '</td>';
            echo '<td><button class="button button-small remove-website" '
               . 'data-collection="' . esc_attr( $data['collection_name'] ?? '' ) . '" '
               . 'style="background:#dc3545;color:white;border-color:#dc3545">Remove</button></td>';
            echo '</tr>';
        }
        echo '</tbody></table>';
    }

    public function check_status() {
        return [
            'openai_key_set'    => ! empty( get_option( 'coc_openai_api_key', '' ) ),
            'vector_db'         => file_exists( $this->vector_db_path . '/chroma.sqlite3' ),
            'python_exists'     => file_exists( $this->python_path ),
            'rag_script_exists' => file_exists( $this->python_script_path ),
        ];
    }
}

endif; // class_exists