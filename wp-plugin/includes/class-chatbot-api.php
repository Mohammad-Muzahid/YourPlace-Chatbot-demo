<?php
// File: includes/class-chatbot-api.php
// Handles OpenAI / external API calls if used alongside RAG

if ( ! defined( 'ABSPATH' ) ) exit;

if ( ! class_exists( 'Chatbot_API' ) ) {
    class Chatbot_API {
        public function __construct() {
            // Add your API hooks here if needed
        }
    }
}