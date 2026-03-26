"""
IM Message Formatter
Platform-specific formatting for different IM channels.
Each platform has its own method; add new ones as needed.
"""
import re


def format_for_telegram(text: str) -> str:
    """
    Convert Markdown to Telegram MarkdownV2-safe format.
    
    Telegram MarkdownV2 supports: *bold* _italic_ `code` ```pre``` ~strikethrough~
    But requires escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    
    Strategy: convert common Markdown to Telegram format, escape the rest.
    """
    if not text:
        return text

    lines = text.split('\n')
    result = []

    for line in lines:
        # Headers → bold
        if line.startswith('# '):
            line = f"*{_tg_escape(line[2:].strip())}*"
        elif line.startswith('## '):
            line = f"*{_tg_escape(line[3:].strip())}*"
        elif line.startswith('### '):
            line = f"*{_tg_escape(line[4:].strip())}*"
        # Horizontal rules
        elif line.strip() in ('---', '───', '***', '___'):
            line = '───────────────'
        # Blockquotes
        elif line.startswith('> '):
            line = f"│ {_tg_escape(line[2:])}"
        # Table rows (simplify)
        elif '|' in line and line.strip().startswith('|'):
            # Skip separator rows like |---|---|
            if re.match(r'^\|[\s\-:|]+\|$', line.strip()):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            line = '  '.join(_tg_escape(c) for c in cells if c)
        else:
            line = _tg_inline(line)

        result.append(line)

    return '\n'.join(result)


def _tg_inline(text: str) -> str:
    """Handle inline Markdown: **bold**, [link](url), `code`"""
    # Protect code blocks first
    parts = []
    i = 0
    while i < len(text):
        # Inline code
        if text[i] == '`' and not text[i:i+3] == '```':
            end = text.find('`', i + 1)
            if end > i:
                parts.append(f"`{text[i+1:end]}`")
                i = end + 1
                continue

        # Bold **text** → *text*
        if text[i:i+2] == '**':
            end = text.find('**', i + 2)
            if end > i:
                inner = _tg_escape(text[i+2:end])
                parts.append(f"*{inner}*")
                i = end + 2
                continue

        # Links [text](url)
        m = re.match(r'\[([^\]]+)\]\(([^)]+)\)', text[i:])
        if m:
            link_text = _tg_escape(m.group(1))
            url = m.group(2)
            parts.append(f"[{link_text}]({url})")
            i += m.end()
            continue

        parts.append(_tg_escape_char(text[i]))
        i += 1

    return ''.join(parts)


def _tg_escape(text: str) -> str:
    """Escape special chars for Telegram MarkdownV2"""
    # These chars must be escaped: _ * [ ] ( ) ~ ` > # + - = | { } . !
    special = r'_[]()~`>#+-=|{}.!'
    result = []
    for c in text:
        if c in special:
            result.append(f'\\{c}')
        else:
            result.append(c)
    return ''.join(result)


def _tg_escape_char(c: str) -> str:
    """Escape a single char for Telegram MarkdownV2"""
    special = r'_[]()~`>#+-=|{}.!'
    if c in special:
        return f'\\{c}'
    return c


def format_for_plain(text: str) -> str:
    """
    Strip Markdown to plain text. 
    Use as fallback or for platforms with no formatting support.
    """
    if not text:
        return text
    # Remove markdown headers
    text = re.sub(r'^#{1,3}\s+', '', text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    # Convert links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    # Remove code markers
    text = text.replace('`', '')
    return text


# Registry: add new formatters here
FORMATTERS = {
    'telegram': format_for_telegram,
    'plain': format_for_plain,
    # 'wechat': format_for_wechat,  # TODO
    # 'signal': format_for_signal,  # TODO
}


def format_message(text: str, channel: str = 'plain') -> str:
    """Format message for a specific channel"""
    formatter = FORMATTERS.get(channel, format_for_plain)
    return formatter(text)
