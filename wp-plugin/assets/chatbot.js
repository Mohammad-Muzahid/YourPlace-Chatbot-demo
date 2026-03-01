/**
 * Chatbot Frontend Widget — chatbot.js
 * - Fixes all JS errors
 * - Maintains full conversation history until chat is closed
 * - Works with Astra theme (no conflicts)
 */
(function ($) {
    'use strict';

    // ── Conversation history (persists until widget is closed) ────────────────
    var conversationHistory = [];

    // ── State ──────────────────────────────────────────────────────────────────
    var isWaiting    = false;
    var elapsedTimer = null;
    var isOpen       = false;

    // ── Wait for DOM ───────────────────────────────────────────────────────────
    $(document).ready(function () {

        // Safety: cocChatbot must be localized by PHP
        if (typeof cocChatbot === 'undefined') {
            console.warn('Chatbot: cocChatbot not defined. Check wp_localize_script.');
            return;
        }

        var $widget  = $('#coc-chatbot-widget');
        if (!$widget.length) {
            console.warn('Chatbot: #coc-chatbot-widget not found in DOM.');
            return;
        }

        var $box      = $('#coc-chatbot-box');
        var $messages = $('#coc-chatbot-messages');
        var $input    = $('#coc-chatbot-input');
        var $send     = $('#coc-chatbot-send');
        var $toggle   = $('#coc-chatbot-toggle');
        var $close    = $('#coc-chatbot-close');

        // ── Toggle open / close ────────────────────────────────────────────────
        $toggle.on('click', function () {
            openChat();
        });

        $close.on('click', function () {
            closeChat();
        });

        function openChat() {
            isOpen = true;
            $box.slideDown(200);
            $toggle.addClass('coc-is-open');
            setTimeout(function () { $input.focus(); }, 220);
            scrollBottom();
        }

        function closeChat() {
            isOpen = false;
            $box.slideUp(200);
            $toggle.removeClass('coc-is-open');
            // Clear conversation history when chat is closed
            conversationHistory = [];
        }

        // ── Send on button click or Enter ──────────────────────────────────────
        $send.on('click', function () { sendMessage(); });

        $input.on('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // ── Core send logic ────────────────────────────────────────────────────
        function sendMessage() {
            if (isWaiting) return;

            var question = $.trim($input.val());
            if (!question) return;

            // Add to history
            conversationHistory.push({ role: 'user', content: question });

            // Show user bubble
            addBubble('user', escHtml(question));
            $input.val('');
            setWaiting(true);

            // Typing indicator + elapsed counter
            var elapsed = 0;
            var $typing = addTyping();
            elapsedTimer = setInterval(function () {
                elapsed++;
                var extra = elapsed > 30
                    ? ' <span style="font-size:10px;opacity:.55">(still thinking…)</span>'
                    : '';
                $typing.find('.coc-elapsed').html(elapsed + 's' + extra);
            }, 1000);

            // Build context string from last 6 exchanges (3 pairs)
            var contextStr = buildContext();

            $.ajax({
                url:  cocChatbot.ajaxUrl,
                type: 'POST',
                data: {
                    action:   'coc_rag_query',
                    question: question,
                    context:  contextStr,
                    nonce:    cocChatbot.nonce
                },
                timeout: 90000, // 90s — OpenAI responds in 2-5s normally
                success: function (resp) {
                    stopTyping($typing);
                    if (resp.success && resp.data && resp.data.answer) {
                        var answer = resp.data.answer;
                        // Save assistant reply to history
                        conversationHistory.push({ role: 'assistant', content: answer });
                        addBubble('bot', formatText(answer));
                    } else {
                        var msg = (resp.data && typeof resp.data === 'string')
                            ? resp.data
                            : 'Sorry, I couldn\'t get an answer right now. Please try again.';
                        addBubble('bot', escHtml(msg), true);
                    }
                },
                error: function (xhr, status) {
                    stopTyping($typing);
                    if (status === 'timeout') {
                        addBubble('bot',
                            'The response is taking longer than expected. Please try again in a moment.',
                            true);
                    } else {
                        addBubble('bot',
                            'Connection error. Please try again.',
                            true);
                        console.error('Chatbot error:', status, xhr.responseText);
                    }
                },
                complete: function () {
                    stopTyping($typing);
                    setWaiting(false);
                    $input.focus();
                }
            });
        }

        // ── Build conversation context for the prompt ──────────────────────────
        function buildContext() {
            // Send last 10 messages (5 exchanges) as context for pronoun resolution
            // Include ALL recent messages including the current question
            var recent = conversationHistory.slice(-10);
            if (recent.length <= 1) return ''; // no prior context yet
            var lines = [];
            // Include everything EXCEPT the very last item (current question sent separately)
            for (var i = 0; i < recent.length - 1; i++) {
                var m = recent[i];
                lines.push((m.role === 'user' ? 'User' : 'Assistant') + ': ' + m.content);
            }
            return lines.join('\n');
        }

        // ── UI helpers ─────────────────────────────────────────────────────────
        function addBubble(role, html, isErr) {
            var cls = role === 'user' ? 'coc-msg-user' : 'coc-msg-bot';
            if (isErr) cls += ' coc-msg-error';
            var $b = $('<div class="coc-bubble ' + cls + '"></div>').html(html);
            $messages.append($b);
            scrollBottom();
            return $b;
        }

        function addTyping() {
            var $t = $(
                '<div class="coc-bubble coc-msg-bot coc-typing">' +
                '<span class="coc-dots">' +
                '<span></span><span></span><span></span>' +
                '</span>' +
                '<span class="coc-elapsed" style="font-size:11px;opacity:.6;margin-left:6px">0s</span>' +
                '</div>'
            );
            $messages.append($t);
            scrollBottom();
            return $t;
        }

        function stopTyping($t) {
            clearInterval(elapsedTimer);
            elapsedTimer = null;
            if ($t && $t.length) $t.remove();
        }

        function setWaiting(state) {
            isWaiting = state;
            $send.prop('disabled', state);
            $input.prop('disabled', state);
        }

        function scrollBottom() {
            if ($messages.length) {
                $messages.scrollTop($messages[0].scrollHeight);
            }
        }

        function escHtml(str) {
            return $('<div>').text(str).html();
        }

        function formatText(str) {
            return escHtml(str)
                .replace(/\n/g, '<br>')
                .replace(/(https?:\/\/[^\s<"]+)/g,
                    '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
        }

    }); // end document.ready

})(jQuery);