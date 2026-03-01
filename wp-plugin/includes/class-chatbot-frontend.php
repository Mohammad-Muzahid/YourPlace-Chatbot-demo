<?php
// File: includes/class-chatbot-frontend.php

if ( ! defined( 'ABSPATH' ) ) exit;

if ( ! class_exists( 'Chatbot_Frontend' ) ) {
    class Chatbot_Frontend {

        public function __construct() {
            add_action( 'wp_enqueue_scripts', [ $this, 'enqueue_assets' ] );
            add_action( 'wp_footer',          [ $this, 'render_widget'  ], 100 );
        }

        public function enqueue_assets() {
            // CSS
            wp_enqueue_style(
                'coc-chatbot',
                COC_PLUGIN_URL . 'assets/chatbot.css',
                [],
                COC_VERSION . '.' . filemtime( COC_PLUGIN_DIR . 'assets/chatbot.css' )
            );

            // JS — depends on jQuery (always available in WP)
            wp_enqueue_script(
                'coc-chatbot',
                COC_PLUGIN_URL . 'assets/chatbot.js',
                [ 'jquery' ],
                COC_VERSION . '.' . filemtime( COC_PLUGIN_DIR . 'assets/chatbot.js' ),
                true  // load in footer
            );

            // Pass data to JS
            wp_localize_script( 'coc-chatbot', 'cocChatbot', [
                'ajaxUrl' => admin_url( 'admin-ajax.php' ),
                'nonce'   => wp_create_nonce( 'coc_chat_nonce' ),
                'siteName' => get_bloginfo( 'name' ),
            ] );
        }

        public function render_widget() {
            $site_name = esc_html( get_bloginfo( 'name' ) ?: 'Your Place Real Estate' );
            ?>
            <div id="coc-chatbot-widget">

                <!-- Floating toggle button (robot icon → X when open) -->
                <button id="coc-chatbot-toggle" aria-label="Open chat assistant" title="Chat with us"></button>

                <!-- Chat box -->
                <div id="coc-chatbot-box" role="dialog" aria-label="Chat assistant">

                    <!-- Header -->
                    <div id="coc-chatbot-header">
                        <div class="coc-header-info">
                            <div class="coc-header-avatar">🤖</div>
                            <div class="coc-header-text">
                                <span class="coc-header-name"><?php echo $site_name; ?></span>
                                <span class="coc-header-status">
                                    <span class="coc-status-dot"></span> Online — AI Assistant
                                </span>
                            </div>
                        </div>
                        <button id="coc-chatbot-close" aria-label="Close chat">✕</button>
                    </div>

                    <!-- Messages -->
                    <div id="coc-chatbot-messages" role="log" aria-live="polite">
                        <div class="coc-bubble coc-msg-bot">
                            👋 Hi! I'm your AI assistant for <?php echo $site_name; ?>.<br>
                            Ask me anything about our properties, services, or listings!
                        </div>
                    </div>

                    <!-- Input -->
                    <div id="coc-chatbot-footer">
                        <input
                            id="coc-chatbot-input"
                            type="text"
                            placeholder="Type your question…"
                            autocomplete="off"
                            maxlength="500"
                        >
                        <button id="coc-chatbot-send" aria-label="Send message"></button>
                    </div>

                    <!-- Powered by -->
                    <div class="coc-powered">Powered by AI · Your conversations are private</div>

                </div><!-- /#coc-chatbot-box -->
            </div><!-- /#coc-chatbot-widget -->
            <?php
        }
    }
}