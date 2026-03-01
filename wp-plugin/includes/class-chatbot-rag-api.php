<?php
/**
 * WordPress plugin RAG handler — API version.
 * Replace class-chatbot-rag.php with this after deploying to Render/Fly.
 * Set the API URL and Admin Key in WordPress Settings → Chatbot.
 */

if ( ! defined( 'ABSPATH' ) ) exit;

class Chatbot_RAG {

    private $api_url;
    private $admin_key;

    public function __construct() {
        $options         = get_option( 'custom_chatbot_options', [] );
        $this->api_url   = rtrim( $options['api_url']   ?? '', '/' );
        $this->admin_key = $options['admin_key'] ?? '';
    }

    /**
     * Answer a user question — calls the cloud API.
     */
    public function answer_question( $question, $context = '' ) {
        if ( empty( $this->api_url ) ) {
            return 'Chatbot API URL not configured. Please set it in Settings → Chatbot.';
        }

        $response = wp_remote_post( $this->api_url . '/query', [
            'timeout'     => 90,
            'headers'     => [ 'Content-Type' => 'application/json' ],
            'body'        => wp_json_encode( [
                'question' => $question,
                'context'  => $context,
            ] ),
        ] );

        if ( is_wp_error( $response ) ) {
            return 'Sorry, I could not connect to the AI service. Please try again.';
        }

        $body = json_decode( wp_remote_retrieve_body( $response ), true );
        return $body['answer'] ?? 'Sorry, I received an unexpected response.';
    }

    /**
     * Upload and process a PDF via the cloud API.
     */
    public function process_pdf_api( $file_path, $file_name ) {
        if ( empty( $this->api_url ) || empty( $this->admin_key ) ) {
            return [ 'success' => false, 'message' => 'API URL or Admin Key not configured.' ];
        }

        $boundary = wp_generate_password( 24, false );
        $body     = "--{$boundary}\r\n";
        $body    .= "Content-Disposition: form-data; name=\"file\"; filename=\"{$file_name}\"\r\n";
        $body    .= "Content-Type: application/pdf\r\n\r\n";
        $body    .= file_get_contents( $file_path ) . "\r\n";
        $body    .= "--{$boundary}--\r\n";

        $response = wp_remote_post( $this->api_url . '/process-pdf', [
            'timeout' => 120,
            'headers' => [
                'Content-Type' => "multipart/form-data; boundary={$boundary}",
                'X-Admin-Key'  => $this->admin_key,
            ],
            'body'    => $body,
        ] );

        if ( is_wp_error( $response ) ) {
            return [ 'success' => false, 'message' => $response->get_error_message() ];
        }

        $data = json_decode( wp_remote_retrieve_body( $response ), true );
        return [
            'success' => isset( $data['status'] ) && $data['status'] === 'ok',
            'message' => $data['message'] ?? $data['error'] ?? 'Unknown response',
        ];
    }

    /**
     * Train on a website URL.
     */
    public function process_website_api( $url, $max_pages = 20 ) {
        $response = wp_remote_post( $this->api_url . '/process-website', [
            'timeout' => 300,
            'headers' => [
                'Content-Type' => 'application/json',
                'X-Admin-Key'  => $this->admin_key,
            ],
            'body' => wp_json_encode( [ 'url' => $url, 'max_pages' => $max_pages ] ),
        ] );

        if ( is_wp_error( $response ) ) {
            return [ 'success' => false, 'message' => $response->get_error_message() ];
        }

        $data = json_decode( wp_remote_retrieve_body( $response ), true );
        return [
            'success' => isset( $data['status'] ) && $data['status'] === 'ok',
            'message' => $data['message'] ?? $data['error'] ?? 'Unknown response',
        ];
    }

    /**
     * Get database stats.
     */
    public function get_stats_api() {
        $response = wp_remote_get( $this->api_url . '/stats', [
            'timeout' => 30,
            'headers' => [ 'X-Admin-Key' => $this->admin_key ],
        ] );

        if ( is_wp_error( $response ) ) return null;
        return json_decode( wp_remote_retrieve_body( $response ), true );
    }
}