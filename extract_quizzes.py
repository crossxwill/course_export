import os
import xml.etree.ElementTree as ET

def parse_manifest(manifest_path):
    tree = ET.parse(manifest_path)
    root = tree.getroot()
    ns = {'ns': 'http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1'}
    quizzes = []
    # build mapping of resource ID to its file hrefs
    id_to_files = {}
    for res in root.findall('.//ns:resource', ns):
        rid = res.get('identifier')
        hrefs = [f.get('href') for f in res.findall('ns:file', ns) if f.get('href')]
        id_to_files[rid] = hrefs
    # collect quiz files from QTI resources and their dependencies
    for res in root.findall('.//ns:resource', ns):
        rtype = res.get('type', '')
        if 'imsqti' in rtype:
            rid = res.get('identifier')
            # direct QTI XML files
            for href in id_to_files.get(rid, []):
                # only include true QTI files (skip CC assessment metadata XML)
                if href.lower().endswith('.qti'):
                    quizzes.append(href)
            # include dependency files (e.g., non_cc_assessments/*.qti)
            for dep in res.findall('ns:dependency', ns):
                depid = dep.get('identifierref')
                for href in id_to_files.get(depid, []):
                    if href.lower().endswith('.qti'):
                        quizzes.append(href)
    return quizzes


def extract_quiz(qti_path):
    tree = ET.parse(qti_path)
    root = tree.getroot()
    # QTI namespace for questestinterop
    qti_ns = {'qti': root.tag.split('}')[0].strip('{')}
    # parse assessment title
    assessment = root.find('.//qti:assessment', qti_ns)
    title = (assessment.get('title') if assessment is not None else None) or os.path.basename(qti_path)
    questions = []
    # iterate through each question item
    for item in root.findall('.//qti:item', qti_ns):
        q = {'id': item.get('ident', '')}
        # parse question_type from metadata if present
        qtype = None
        for meta in item.findall('.//qti:qtimetadatafield', qti_ns):
            label = meta.find('qti:fieldlabel', qti_ns)
            entry = meta.find('qti:fieldentry', qti_ns)
            if label is not None and label.text == 'question_type' and entry is not None:
                qtype = entry.text.strip()
        q['type'] = qtype or 'unknown'
        # capture raw prompt HTML/text for inline blanks
        prompt_elem = item.find('.//qti:mattext', qti_ns)
        raw_prompt = prompt_elem.text or ''
        # store both raw and plain prompt text
        q['prompt_html'] = raw_prompt
        q['prompt'] = raw_prompt
        # handle fill-in blanks or standard choices
        if qtype and qtype.startswith('fill_in'):
            blanks = []
            # group options for each blank
            for lid in item.findall('.//qti:response_lid', qti_ns):
                raw_id = lid.get('ident', '')
                lid_id = raw_id.replace('response_', '')
                opts = []
                # find all response_label under this lid
                for label in lid.findall('.//qti:response_label', qti_ns):
                    mat = label.find('.//qti:mattext', qti_ns)
                    if mat is not None and mat.text:
                        opts.append(mat.text.strip())
                blanks.append({'ident': lid_id, 'options': opts})
            q['blanks'] = blanks
        else:
            choices = []
            for label in item.findall('.//qti:response_label', qti_ns):
                mat = label.find('.//qti:mattext', qti_ns)
                if mat is not None and mat.text:
                    choices.append(mat.text.strip())
            q['choices'] = choices
        questions.append(q)
    return title, questions


def render_html(title, questions):
    html = []
    html.append(f"<!DOCTYPE html>")
    html.append(f"<html lang='en'>")
    html.append(f"<head>")
    html.append(f"<meta charset='UTF-8'>")
    html.append(f"<title>{title}</title>")
    html.append(f"<link rel='stylesheet' href='style.css'>")
    html.append("<script src='https://code.jquery.com/jquery-3.6.0.min.js'></script>")
    html.append("<script src='https://code.jquery.com/ui/1.12.1/jquery-ui.min.js'></script>")
    html.append("<script>")
    html.append("  $(function() {")
    html.append("    $('.sortable').sortable();")
    html.append("    $('.sortable').disableSelection();")
    html.append("    $('.draggable').draggable({")
    html.append("      connectToSortable: '.sortable, .dropzone',")
    html.append("      helper: 'clone',")
    html.append("      revert: 'invalid'")
    html.append("    });")
    html.append("    $('.dropzone').droppable({")
    html.append("      accept: '.draggable',")
    html.append("      hoverClass: 'drop-hover',")
    html.append("      drop: function(event, ui) {")
    html.append("        var $this = $(this);")
    html.append("        ui.draggable.position({ of: $this, my: 'left top', at: 'left top' });")
    html.append("        ui.draggable.appendTo($this);")
    html.append("      }")
    html.append("    });")
    html.append("  });")
    html.append("</script>")
    html.append(f"</head>")
    html.append(f"<body>")
    html.append(f"<h1>{title}</h1>")
    html.append(f"<form id='quiz-form'>")
    for idx, q in enumerate(questions, 1):
        html.append(f"<div class='question'>")
        html.append(f"<p class='qtype'>Type: {q.get('type', 'unknown')}</p>")
        # inline fill-in-the-blank by embedding selects into the prompt text
        if q.get('type', '').startswith('fill_in') and 'blanks' in q:
            prompt_html = q.get('prompt_html', '')
            # replace each placeholder with its dropdown
            for b_idx, blank in enumerate(q['blanks'], 1):
                ident = blank.get('ident')
                options_html = ''.join(f"<option value='{opt}'>{opt}</option>" for opt in blank.get('options', []))
                select_html = f"<select name='q{idx}_{b_idx}' class='blank-select'>" + options_html + "</select>"
                prompt_html = prompt_html.replace(f"[{ident}]", select_html)
            html.append(f"<p class='prompt'>{idx}. {prompt_html}</p>")
        elif q.get('type') == 'matching' and 'blanks' in q:
            html.append(f"<p class='prompt'>{idx}. {q['prompt']}</p>")
            html.append("<div class='match-container'>")
            # Render drop zones
            html.append("<div class='match-zones'>")
            for i, blank in enumerate(q['blanks'], 1):
                html.append(f"<div class='dropzone' data-match-id='opt{i}'></div>")
            html.append("</div>")
            # Render draggable options
            html.append("<ul class='match-options'>")
            for i, blank in enumerate(q['blanks'], 1):
                for opt in blank.get('options', []):
                    html.append(f"<li class='draggable' data-match-id='opt{i}'>{opt}</li>")
            html.append("</ul>")
            html.append("</div>")
        elif q.get('type') == 'ordering':
            html.append(f"<p class='prompt'>{idx}. {q['prompt']}</p>")
            html.append("<ul class='choices sortable'>")
            for choice in q.get('choices', []):
                html.append(f"<li class='draggable choice'>{choice}</li>")
            html.append("</ul>")
        else:
            html.append(f"<p class='prompt'>{idx}. {q['prompt']}</p>")
            html.append(f"<ul class='choices'>")
            for choice in q.get('choices', []):
                html.append(f"<li><input type='radio' name='q{idx}'> {choice}</li>")
            html.append(f"</ul>")
        html.append(f"</div>")
    html.append(f"<button type='submit'>Submit Answers</button>")
    html.append(f"</form>")
    html.append(f"</body>")
    html.append(f"</html>")
    return '\n'.join(html)


def main():
    base_dir = os.path.dirname(__file__)
    manifest = os.path.join(base_dir, 'imsmanifest.xml')
    out_dir = os.path.join(base_dir, 'quizzes_html')
    os.makedirs(out_dir, exist_ok=True)
    # create default CSS and JS for quizzes
    css_path = os.path.join(out_dir, 'style.css')
    if not os.path.exists(css_path):
        with open(css_path, 'w', encoding='utf-8') as f:
            f.write("""
body { font-family: Arial, sans-serif; max-width: 800px; margin: 2em auto; padding: 1em; background: #fff; color: #333; }
.question { background: #f9f9f9; border: 1px solid #ddd; padding: 1em; margin-bottom: 1em; border-radius: 4px; }
.prompt { font-weight: bold; margin-bottom: 0.5em; }
.qtype { font-style: italic; color: #555; margin: 0 0 0.5em 0; }
.choices { list-style: none; padding: 0; }
.choices li { margin: 0.3em 0; }
button { background: #007bff; color: #fff; border: none; padding: 0.6em 1.2em; font-size: 1em; border-radius: 4px; cursor: pointer; }
button:hover { background: #0056b3; }
""")
    js_path = os.path.join(out_dir, 'script.js')
    if not os.path.exists(js_path):
        with open(js_path, 'w', encoding='utf-8') as f:
            f.write("""
document.getElementById('quiz-form').addEventListener('submit', function(e) {
    e.preventDefault();
    alert('Your answers have been submitted!');
});
""")
    quizzes = parse_manifest(manifest)
    for quiz_file in quizzes:
        qti_path = os.path.join(base_dir, quiz_file)
        title, questions = extract_quiz(qti_path)
        safe_name = ''.join(c if c.isalnum() else '_' for c in title) + '.html'
        html_content = render_html(title, questions)
        with open(os.path.join(out_dir, safe_name), 'w', encoding='utf-8') as f:
            f.write(html_content)
    print(f"Extracted {len(quizzes)} quizzes to {out_dir}")

if __name__ == '__main__':
    main()
