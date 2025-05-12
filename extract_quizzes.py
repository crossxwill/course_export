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

    # Helper function to get content from mattext elements, preserving HTML
    def get_mattext_content_for_extract(mattext_element_node):
        if mattext_element_node is None:
            return ""
        # Reconstruct inner content, including text and child elements as HTML strings
        content = (mattext_element_node.text or "")
        for child_node in mattext_element_node:
            # Use method='html' for ET.tostring to handle potentially non-XML-compliant HTML (e.g., <br> without closing)
            content += ET.tostring(child_node, encoding='unicode', method='html')
        return content.strip()

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
        
        # Parse question prompt using the helper
        question_material_elem = item.find('qti:presentation/qti:material', qti_ns)
        prompt_mattext_elem = None
        if question_material_elem is not None:
            prompt_mattext_elem = question_material_elem.find('qti:mattext', qti_ns)
        
        raw_prompt_html = get_mattext_content_for_extract(prompt_mattext_elem)
        q['prompt_html'] = raw_prompt_html
        # For plain prompt, ideally strip HTML. For now, use the HTML version or a simple text iter.
        q['prompt'] = "".join(prompt_mattext_elem.itertext()).strip() if prompt_mattext_elem is not None else raw_prompt_html

        # handle fill-in blanks, categorization, ordering, or standard choices
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
        elif qtype == 'categorization_question':
            categories = []
            items_to_categorize = []
            presentation_node = item.find('.//qti:presentation', qti_ns)
            if presentation_node is not None:
                # Extract categories and items
                first_response_lid = True # To ensure items are extracted only once
                for resp_lid in presentation_node.findall('qti:response_lid', qti_ns):
                    category_material = resp_lid.find('qti:material', qti_ns)
                    category_name = ''
                    if category_material is not None:
                        mattext_node = category_material.find('qti:mattext', qti_ns)
                        if mattext_node is not None and mattext_node.text:
                            category_name = mattext_node.text.strip()
                    categories.append({'ident': resp_lid.get('ident'), 'name': category_name})

                    if first_response_lid:
                        render_choice_node = resp_lid.find('.//qti:render_choice', qti_ns) # Look deeper if nested
                        if render_choice_node is not None:
                            for resp_label in render_choice_node.findall('qti:response_label', qti_ns):
                                item_text_node = resp_label.find('.//qti:mattext', qti_ns)
                                item_text = (item_text_node.text.strip() 
                                             if item_text_node is not None and item_text_node.text 
                                             else 'Unknown Item')
                                items_to_categorize.append({'ident': resp_label.get('ident'), 
                                                            'text': item_text})
                        first_response_lid = False
            q['categories'] = categories
            q['items_to_categorize'] = items_to_categorize
        elif qtype == 'ordering_question':
            choices = []
            # Specific path for ordering items: item -> presentation -> response_lid -> render_extension -> ims_render_object -> flow_label -> response_label
            flow_label_elem = item.find('.//qti:response_lid/qti:render_extension/qti:ims_render_object/qti:flow_label', qti_ns)
            if flow_label_elem is not None:
                for label in flow_label_elem.findall('qti:response_label', qti_ns):
                    mat = label.find('.//qti:mattext', qti_ns) # .// to be safe if material/mattext is nested
                    choice_html = get_mattext_content_for_extract(mat)
                    if choice_html: # Ensure we don't add empty choices
                        choices.append(choice_html)
            q['choices'] = choices
        elif qtype == 'matching_question':
            match_prompts = []
            match_options = []
            first_lid = True
            # Iterate over each response_lid, each representing a term to be matched
            for lid in item.findall('.//qti:presentation/qti:response_lid', qti_ns):
                prompt_material_elem = lid.find('qti:material/qti:mattext', qti_ns)
                if prompt_material_elem is not None:
                    match_prompts.append({
                        'id': lid.get('ident'),
                        'text': get_mattext_content_for_extract(prompt_material_elem)
                    })

                # Options are typically the same for all terms in a matching question;
                # extract them from the first response_lid encountered.
                if first_lid:
                    render_choice_elem = lid.find('qti:render_choice', qti_ns)
                    if render_choice_elem is not None:
                        for label in render_choice_elem.findall('qti:response_label', qti_ns):
                            option_mat_elem = label.find('.//qti:mattext', qti_ns) # .// to be safe
                            if option_mat_elem is not None:
                                option_text = get_mattext_content_for_extract(option_mat_elem)
                                if option_text: # Ensure we don't add empty options
                                    match_options.append(option_text)
                    first_lid = False
            q['match_prompts'] = match_prompts
            q['match_options'] = match_options
        elif qtype == 'multiple_answers_question':
            q['type'] = 'multiple_answers_question'
            prompt_material = item.find('.//qti:presentation/qti:material/qti:mattext', qti_ns)
            if prompt_material is not None:
                q['prompt_html'] = get_mattext_content_for_extract(prompt_material)
                q['prompt'] = prompt_material.text  # Fallback or plain text

            choices = []
            choices_container = item.find('.//qti:presentation/qti:response_lid[@rcardinality="Multiple"]/qti:render_choice', qti_ns)
            if choices_container is not None:
                for label in choices_container.findall('qti:response_label', qti_ns):
                    choice_mattext = label.find('qti:material/qti:mattext', qti_ns)
                    if choice_mattext is not None:
                        choices.append(get_mattext_content_for_extract(choice_mattext))
            q['choices'] = choices
        else: # Handles multiple_choice, true_false, numerical (if it had choices), etc.
            choices = []
            # General path for choices, typically under render_choice
            # This path might need to be more specific if it incorrectly picks up other response_labels.
            # For typical multiple choice: item -> presentation -> response_lid -> render_choice -> response_label
            # Using .//qti:response_label to find all response_labels under the item.
            for label in item.findall('.//qti:response_label', qti_ns):
                # Check if this label is part of a render_choice structure to be more specific for MCQs
                # This is a simple check; more robust would be to check parent tags.
                # For now, we assume any response_label not caught by specific types above might be a choice.
                mat = label.find('.//qti:mattext', qti_ns)
                choice_html = get_mattext_content_for_extract(mat)
                if choice_html:
                    choices.append(choice_html)
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
        elif q.get('type') == 'ordering_question': # Changed from 'ordering' to 'ordering_question'
            html.append(f"<p class='prompt'>{idx}. {q.get('prompt_html', q.get('prompt', ''))}</p>") # Use prompt_html
            html.append("<ul class='choices sortable'>") # jQuery UI sortable will target this
            for choice in q.get('choices', []): # Choices now contain HTML if present
                html.append(f"<li class='choice'>{choice}</li>") # Removed 'draggable' class
            html.append("</ul>")
        elif q.get('type') == 'matching_question':
            html.append(f"<p class='prompt'>{idx}. {q.get('prompt_html', q.get('prompt', ''))}</p>")
            if q.get('match_prompts') and q.get('match_options'):
                html.append("<div class='matching-items-container'>")
                for mp_idx, match_prompt_item in enumerate(q['match_prompts']):
                    html.append("<div class='match-item' style='margin-bottom: 0.5em; display: flex; align-items: center; padding-left: 1em;'>")
                    html.append(f"<span class='match-prompt-text' style='margin-right: 1em; flex-basis: 50%;'>{match_prompt_item.get('text', '')}</span>")
                    
                    select_name = f"q{idx}_match_{mp_idx}_{match_prompt_item.get('id', mp_idx)}"
                    select_html = f"<select name='{select_name}' class='match-options-select' style='flex-basis: 50%;'>"
                    select_html += "<option value=''>Select...</option>"
                    for option_text in q['match_options']:
                        # Using html.escape for option text and value to be safe
                        escaped_option_text = option_text.replace("<", "&lt;").replace(">", "&gt;") # Basic escape
                        select_html += f"<option value='{escaped_option_text}'>{escaped_option_text}</option>"
                    select_html += "</select>"
                    html.append(select_html)
                    html.append("</div>")
                html.append("</div>")
            else:
                html.append("<p><em>Matching question is missing prompts or options.</em></p>")
        elif q.get('type') == 'categorization_question':
            html.append(f"<p class='prompt'>{idx}. {q.get('prompt_html', q.get('prompt', ''))}</p>") # Use prompt_html
            html.append("<div class='categorization-container'>")
            if q.get('items_to_categorize'):
                for item_idx, item_to_cat in enumerate(q.get('items_to_categorize', [])):
                    html.append("<div class='categorization-item' style='margin-bottom: 0.5em; display: flex; align-items: center;'>") # Added basic styling
                    html.append(f"<span class='item-text' style='margin-right: 1em;'>{item_to_cat['text']}</span>")
                    # Create a select dropdown for categories
                    # Ensure unique name for each select: q{question_index}_item_{item_identifier_or_index}
                    select_name = f"q{idx}_item_{item_to_cat.get('ident', item_idx)}"
                    select_html = f"<select name='{select_name}' class='category-select'>"
                    select_html += "<option value=''>Select category...</option>" # Default option
                    if q.get('categories'):
                        for cat in q.get('categories', []):
                            select_html += f"<option value='{cat.get('ident', '')}'>{cat.get('name', 'Unnamed Category')}</option>"
                    select_html += "</select>"
                    html.append(select_html)
                    html.append("</div>")
            else:
                html.append("<p><em>No items found for categorization.</em></p>")
            if not q.get('categories'):
                html.append("<p><em>No categories found for categorization.</em></p>")
            html.append("</div>")
        elif q.get('type') == 'numerical_question':
            html.append(f"<p class='prompt'>{idx}. {q['prompt']}</p>")
            # Use a unique name for the input field, e.g., q{question_index}_numerical
            input_name = f"q{idx}_numerical"
            html.append(f"<input type='number' name='{input_name}' class='numerical-input' step='any'>") # step='any' allows decimals
        elif q.get('type') == 'multiple_answers_question':
            html.append(f"<p class='prompt'>{idx}. {q.get('prompt_html', q.get('prompt', ''))}</p>")
            html.append("<div class='choices'>")
            if q.get('choices'):
                for i, choice_html in enumerate(q['choices']):
                    checkbox_id = f"q{idx}_choice{i}"
                    html.append(f"<div class='choice'><input type='checkbox' id='{checkbox_id}' name='q{idx}_ans' value='{i}'><label for='{checkbox_id}'>{choice_html}</label></div>")
            html.append("</div>")
        else:
            html.append(f"<p class='prompt'>{idx}. {q.get('prompt_html', q.get('prompt', ''))}</p>") # Use prompt_html
            html.append(f"<ul class='choices'>")
            for choice in q.get('choices', []):
                # Default to radio buttons for unspecified types or simple multiple choice
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
