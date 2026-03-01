<?php
/**
 * Plugin Name: Custom OpenAI Chatbot
 * Plugin URI:  https://your-site.local
 * Description: RAG-powered chatbot using local Ollama + ChromaDB knowledge base
 * Version:     2.0.0
 * Author:      Your Name
 * License:     GPL v2 or later
 */

if ( ! defined( 'ABSPATH' ) ) exit;

if ( ! defined( 'COC_VERSION' ) ) {
    define( 'COC_VERSION',    '2.0.0' );
    define( 'COC_PLUGIN_DIR', plugin_dir_path( __FILE__ ) );
    define( 'COC_PLUGIN_URL', plugin_dir_url( __FILE__ ) );
}

function coc_require( $relative_path ) {
    $full = COC_PLUGIN_DIR . $relative_path;
    if ( file_exists( $full ) ) {
        require_once $full;
        return true;
    }
    add_action( 'admin_notices', function() use ( $relative_path ) {
        echo '<div class="notice notice-error"><p><strong>Custom OpenAI Chatbot:</strong> Missing file — '
           . esc_html( $relative_path ) . '. Please re-upload this file.</p></div>';
    } );
    error_log( "CustomOpenAIChatbot: MISSING FILE — {$relative_path}" );
    return false;
}

coc_require( 'includes/class-chatbot-admin.php' );
coc_require( 'includes/class-chatbot-api.php' );
coc_require( 'includes/class-chatbot-frontend.php' );
coc_require( 'includes/class-chatbot-rag.php' );
// NOTE: class-chatbot-website.php must NOT exist — delete it if present.

class CustomOpenAIChatbot {
    private static $instance = null;

    public static function get_instance() {
        if ( null === self::$instance ) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    private function __construct() {
        add_action( 'plugins_loaded', [ $this, 'init' ], 10 );
    }

    public function init() {
        if ( class_exists( 'Chatbot_Admin' ) )    new Chatbot_Admin();
        if ( class_exists( 'Chatbot_API' ) )      new Chatbot_API();
        if ( class_exists( 'Chatbot_Frontend' ) ) new Chatbot_Frontend();
        if ( class_exists( 'Chatbot_RAG' ) )      new Chatbot_RAG();
    }
}

CustomOpenAIChatbot::get_instance();