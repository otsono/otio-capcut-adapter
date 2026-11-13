"""CapCut text: what a caption is made of in a draft.

The shapes and default values here are what CapCut 8.7 writes for a plain
caption typed into the app, so a caption from this adapter carries the same
fields as one made by hand.
"""

import copy
import json
import uuid
from typing import Any, Dict, Optional

#: The style CapCut gives new text: white, size 15, in its default font.
DEFAULT_FONT_SIZE = 15.0


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def utf16_length(text: str) -> int:
    """Length in UTF-16 code units, which is how CapCut counts a style's range.

    Not ``len(text)``: an emoji is one Python character and two UTF-16 units,
    and a range one unit short leaves the last character unstyled.
    """
    return len(text.encode("utf-16-le")) // 2


def content_json(text: str, font_size: float = DEFAULT_FONT_SIZE) -> str:
    """The ``content`` string CapCut stores: the words plus one style covering them."""
    return json.dumps(
        {
            "text": text,
            "styles": [{
                "fill": {"content": {"render_type": "solid", "solid": {"color": [1, 1, 1]}}},
                # An empty font path is CapCut's default font on any computer;
                # the app's own drafts name a path inside its install folder.
                "font": {"path": "", "id": ""},
                "size": font_size,
                "range": [0, utf16_length(text)],
            }],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def text_from_content(content: Any) -> Optional[str]:
    """The words of a text material's ``content``, which is JSON in newer drafts and plain text in old ones."""
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except ValueError:
        return content
    if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
        return parsed["text"]
    return content


#: Every field CapCut 8.7 writes on a text material, at its values for new, unstyled text.
TEXT_MATERIAL_DEFAULTS: Dict[str, Any] = {
    "recognize_task_id": "", "name": "", "recognize_text": "", "recognize_model": "", "punc_model": "",
    "type": "text", "base_content": "",
    "words": {"start_time": [], "end_time": [], "text": []},
    "current_words": {"start_time": [], "end_time": [], "text": []},
    "global_alpha": 1.0, "combo_info": {"text_templates": []},
    "caption_template_info": {"resource_id": "", "third_resource_id": "", "resource_name": "", "category_id": "",
                              "category_name": "", "effect_id": "", "request_id": "", "path": "", "is_new": False,
                              "source_platform": 0},
    "layer_weight": 1, "letter_spacing": 0.0, "text_curve": None, "text_loop_on_path": False, "offset_on_path": 0.0,
    "enable_path_typesetting": False, "text_exceeds_path_process_type": 0, "text_typesetting_paths": None,
    "text_typesetting_paths_file": "", "text_typesetting_path_index": 0, "line_spacing": 0.02,
    "has_shadow": False, "shadow_color": "", "shadow_alpha": 0.9, "shadow_smoothing": 0.45, "shadow_distance": 5.0,
    "shadow_point": {"x": 0.6363961030678928, "y": -0.6363961030678928}, "shadow_angle": -45.0,
    "shadow_thickness_projection_enable": False, "shadow_thickness_projection_angle": 0.0,
    "shadow_thickness_projection_distance": 0.0, "border_alpha": 1.0, "border_color": "", "border_width": 0.08,
    "border_mode": 0, "style_name": "", "text_color": "#FFFFFF", "text_alpha": 1.0, "font_name": "",
    "font_title": "none", "font_size": DEFAULT_FONT_SIZE, "font_path": "", "font_id": "", "font_resource_id": "",
    "initial_scale": 1.0, "font_url": "", "typesetting": 0, "alignment": 1, "line_feed": 1,
    "use_effect_default_color": True, "is_rich_text": False, "shape_clip_x": False, "shape_clip_y": False,
    "ktv_color": "", "text_to_audio_ids": [], "bold_width": 0.0, "italic_degree": 0, "underline": False,
    "underline_width": 0.05, "underline_offset": 0.22, "sub_type": 0, "check_flag": 7, "text_size": 30,
    "font_category_name": "", "font_source_platform": 0, "font_third_resource_id": "", "font_category_id": "",
    "add_type": 0, "operation_type": 0, "recognize_type": 0, "fonts": [], "background_color": "",
    "background_alpha": 1.0, "background_style": 0, "background_round_radius": 0.0, "background_width": 0.14,
    "background_height": 0.14, "background_vertical_offset": 0.0, "background_horizontal_offset": 0.0,
    "background_fill": "", "single_char_bg_enable": False, "single_char_bg_color": "", "single_char_bg_alpha": 1.0,
    "single_char_bg_round_radius": 0.3, "single_char_bg_width": 0.0, "single_char_bg_height": 0.0,
    "single_char_bg_vertical_offset": 0.0, "single_char_bg_horizontal_offset": 0.0, "font_team_id": "",
    "tts_auto_update": False, "text_preset_resource_id": "", "group_id": "", "preset_id": "", "preset_name": "",
    "preset_category": "", "preset_category_id": "", "preset_index": 0, "preset_has_set_alignment": False,
    "force_apply_line_max_width": False, "language": "", "relevance_segment": [], "original_size": [],
    "fixed_width": -1.0, "fixed_height": -1.0, "line_max_width": 0.82, "oneline_cutoff": False,
    "cutoff_postfix": "", "subtitle_template_original_fontsize": 0.0, "subtitle_keywords": None,
    "inner_padding": -1.0, "multi_language_current": "none", "source_from": "", "is_lyric_effect": False,
    "lyric_group_id": "",
    "lyrics_template": {"resource_id": "", "resource_name": "", "panel": "", "effect_id": "", "path": "",
                        "category_id": "", "category_name": "", "request_id": ""},
    "is_batch_replace": False, "is_words_linear": False, "ssml_content": "", "subtitle_keywords_config": None,
    "sub_template_id": -1, "translate_original_text": "",
}

#: Every field CapCut 8.7 writes on a text segment that differs from a video one, or is text-only.
TEXT_SEGMENT_FIELDS: Dict[str, Any] = {
    "source_timerange": None, "render_timerange": {"start": 0, "duration": 0}, "desc": "", "state": 0,
    "speed": 1.0, "is_loop": False, "is_tone_modify": False, "reverse": False, "intensifies_audio": False,
    "cartoon": False, "volume": 1.0, "last_nonzero_volume": 1.0,
    "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0},
             "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
    "uniform_scale": {"on": True, "value": 1.0}, "render_index": 14000, "keyframe_refs": [],
    "enable_lut": False, "enable_adjust": False, "enable_hsl": False, "visible": True, "group_id": "",
    "enable_color_curves": True, "enable_hsl_curves": True, "track_render_index": 1, "hdr_settings": None,
    "enable_color_wheels": True, "track_attribute": 0, "is_placeholder": False, "template_id": "",
    "enable_smart_color_adjust": False, "template_scene": "default", "common_keyframes": [], "caption_info": None,
    "responsive_layout": {"enable": False, "target_follow": "", "size_layout": 0, "horizontal_pos_layout": 0,
                          "vertical_pos_layout": 0},
    "enable_color_match_adjust": False, "enable_color_correct_adjust": False, "enable_adjust_mask": False,
    "raw_segment_id": "", "lyric_keyframes": None, "enable_video_mask": True,
    "digital_human_template_group_id": "", "color_correct_alg_result": "", "source": "segmentsourcenormal",
    "enable_mask_stroke": False, "enable_mask_shadow": False, "enable_color_adjust_pro": False,
}


def text_material(text: str, font_size: float = DEFAULT_FONT_SIZE) -> Dict[str, Any]:
    material = copy.deepcopy(TEXT_MATERIAL_DEFAULTS)
    material["id"] = _new_id()
    material["content"] = content_json(text, font_size)
    material["font_size"] = font_size
    return material


def text_animation() -> Dict[str, Any]:
    """The one companion CapCut gives a text segment: an empty animation list."""
    return {"id": _new_id(), "type": "sticker_animation", "animations": [], "multi_language_current": "none"}


def text_segment(material_id: str, animation_id: str, start: int, duration: int, y: float) -> Dict[str, Any]:
    segment = copy.deepcopy(TEXT_SEGMENT_FIELDS)
    segment["id"] = _new_id()
    segment["material_id"] = material_id
    segment["extra_material_refs"] = [animation_id]
    segment["target_timerange"] = {"start": start, "duration": duration}
    segment["clip"]["transform"]["y"] = y
    return segment
