from app.utils.text_compact import compact_message_spacing


def test_three_blank_lines_collapse_to_one_blank_line():
    assert compact_message_spacing('A\n\n\nB') == 'A\n\nB'
    assert compact_message_spacing('A\n\n\n\n\nB') == 'A\n\nB'


def test_two_blank_lines_are_preserved():
    text = 'A\n\nB\n\nC'
    assert compact_message_spacing(text) == text


def test_pre_block_keeps_internal_spacing():
    text = '<b>Логи</b>\n\n\n<pre>line1\n\n\nline2</pre>\n\n\n<b>Конец</b>'
    result = compact_message_spacing(text)

    assert '<pre>line1\n\n\nline2</pre>' in result
    assert result.startswith('<b>Логи</b>\n\n<pre>')
    assert result.endswith('</pre>\n\n<b>Конец</b>')


def test_empty_and_short_texts_are_unchanged():
    assert compact_message_spacing('') == ''
    assert compact_message_spacing('A\nB') == 'A\nB'
