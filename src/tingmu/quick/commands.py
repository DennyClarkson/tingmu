"""Authoritative UI commands. This registry is never an LLM tool schema."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class CommandSpec:
    domain: str
    clears_status: bool
    handler: tuple[str, str]
    feature: bool = False


COMMANDS = MappingProxyType(
    {
        "addFiles": CommandSpec("media", False, ("dialogs", "command_openFiles"), False),
        "addPaths": CommandSpec("media", False, ("library_controller", "command_addPaths"), False),
        "askNotebook": CommandSpec("chat", False, ("learning", "command_ask_notebook"), True),
        "clearChat": CommandSpec("chat", False, ("chat", "command_clear_chat"), False),
        "clearCompleted": CommandSpec("queue", False, ("queue_controller", "command_clearCompleted"), False),
        "clearLoop": CommandSpec("media", False, ("playback_controller", "command_clearLoop"), False),
        "copyFiles": CommandSpec("media", False, ("dialogs", "command_openFiles"), False),
        "copyText": CommandSpec("notification", False, ("chat", "command_copy_text"), True),
        "deleteChat": CommandSpec("chat", False, ("chat", "command_rename_chat"), True),
        "deleteEntry": CommandSpec("learning", False, ("learning", "command_delete_entry"), False),
        "deleteModel": CommandSpec("models", False, ("models", "command_delete_model"), True),
        "discoverModels": CommandSpec("models", False, ("models", "command_discover_models"), True),
        "dismissError": CommandSpec(
            "notification", True, ("application_controller", "command_dismiss_error"), False
        ),
        "editChat": CommandSpec("chat", False, ("chat", "command_retry_chat"), True),
        "enqueue": CommandSpec("queue", False, ("queue_controller", "command_enqueue"), False),
        "exportDiagnostics": CommandSpec(
            "notification", False, ("application_controller", "command_export_diagnostics"), False
        ),
        "exportNotebook": CommandSpec("learning", False, ("learning", "command_export_notebook"), True),
        "exportNotes": CommandSpec("learning", False, ("learning", "command_export_notes"), False),
        "exportSubtitle": CommandSpec("media", False, ("dialogs", "command_exportSubtitle"), False),
        "exportTranslation": CommandSpec("media", False, ("dialogs", "command_exportSubtitle"), False),
        "importStudySubtitle": CommandSpec("queue", False, ("dialogs", "command_importSubtitle"), False),
        "importSubtitle": CommandSpec("media", False, ("dialogs", "command_importSubtitle"), False),
        "loopA": CommandSpec("media", False, ("playback_controller", "command_loopA"), False),
        "loopB": CommandSpec("media", False, ("playback_controller", "command_loopB"), False),
        "loopCue": CommandSpec("media", False, ("playback_controller", "command_repeatCue"), False),
        "newChat": CommandSpec("chat", False, ("chat", "command_new_chat"), True),
        "nextCue": CommandSpec("media", False, ("playback_controller", "command_nextCue"), False),
        "nextMedia": CommandSpec("media", False, ("playback_controller", "command_nextMedia"), False),
        "openAbout": CommandSpec(
            "notification", False, ("application_controller", "command_open_about"), False
        ),
        "openDataFolder": CommandSpec(
            "notification", False, ("application_controller", "command_open_data_folder"), False
        ),
        "openEntry": CommandSpec("learning", False, ("learning", "command_open_entry"), False),
        "openFiles": CommandSpec("media", False, ("dialogs", "command_openFiles"), False),
        "openFolder": CommandSpec("media", False, ("dialogs", "command_openFolder"), False),
        "openLessonSettings": CommandSpec(
            "notification", False, ("queue_controller", "command_openLessonSettings"), False
        ),
        "openLink": CommandSpec("notification", False, ("learning", "command_open_link"), True),
        "openLogFolder": CommandSpec(
            "notification", False, ("application_controller", "command_open_log_folder"), False
        ),
        "openMedia": CommandSpec("media", False, ("library_controller", "command_openMedia"), False),
        "openNotebook": CommandSpec("learning", False, ("learning", "command_open_notebook"), True),
        "organizeNotebook": CommandSpec("learning", False, ("learning", "command_organize_notebook"), True),
        "pauseQueue": CommandSpec("queue", False, ("queue_controller", "command_pauseQueue"), False),
        "playPause": CommandSpec("media", False, ("playback_controller", "command_playPause"), False),
        "practiceNotebook": CommandSpec("learning", False, ("learning", "command_practice_notebook"), True),
        "prepareCurrent": CommandSpec("queue", False, ("queue_controller", "command_prepareCurrent"), False),
        "prepareLessons": CommandSpec("queue", False, ("queue_controller", "command_prepareLessons"), False),
        "previousCue": CommandSpec("media", False, ("playback_controller", "command_previousCue"), False),
        "previousMedia": CommandSpec("media", False, ("playback_controller", "command_previousMedia"), False),
        "refreshDiagnostics": CommandSpec(
            "notification", False, ("application_controller", "command_refresh_diagnostics"), False
        ),
        "refreshModels": CommandSpec("models", False, ("models", "command_refresh_models"), True),
        "removeDisplaySubtitle": CommandSpec(
            "media", False, ("playback_controller", "command_removeDisplaySubtitle"), False
        ),
        "removeJob": CommandSpec("queue", False, ("queue_controller", "command_removeJob"), False),
        "removeMedia": CommandSpec("media", False, ("library_controller", "command_removeMedia"), False),
        "renameChat": CommandSpec("chat", False, ("chat", "command_rename_chat"), True),
        "repeatCue": CommandSpec("media", False, ("playback_controller", "command_repeatCue"), False),
        "resetJobs": CommandSpec("queue", False, ("queue_controller", "command_resetJobs"), True),
        "resetMedia": CommandSpec("queue", False, ("queue_controller", "command_resetJobs"), True),
        "retryChat": CommandSpec("chat", False, ("chat", "command_retry_chat"), True),
        "retryFailed": CommandSpec("queue", False, ("queue_controller", "command_retryFailed"), False),
        "retryJob": CommandSpec("queue", False, ("queue_controller", "command_retryJob"), False),
        "reviewEntry": CommandSpec("learning", False, ("learning", "command_review_entry"), True),
        "saveFrame": CommandSpec("media", False, ("playback_controller", "command_saveFrame"), False),
        "saveModel": CommandSpec("models", False, ("models", "command_save_model"), True),
        "saveNote": CommandSpec("learning", False, ("learning", "command_save_note"), False),
        "saveNotebook": CommandSpec("learning", False, ("learning", "command_save_notebook"), True),
        "saveSelection": CommandSpec("learning", False, ("learning", "command_save_word"), False),
        "saveSentence": CommandSpec("learning", False, ("learning", "command_save_sentence"), False),
        "saveWord": CommandSpec("learning", False, ("learning", "command_save_word"), False),
        "scanWorkspaceMedia": CommandSpec(
            "media", False, ("library_controller", "command_scanWorkspaceMedia"), False
        ),
        "seek": CommandSpec("media", False, ("playback_controller", "command_seek"), False),
        "seekRelative": CommandSpec("media", False, ("playback_controller", "command_seekRelative"), False),
        "selectChat": CommandSpec("chat", False, ("chat", "command_select_chat"), True),
        "selectModel": CommandSpec("models", False, ("models", "command_select_model"), True),
        "selectNotebook": CommandSpec("learning", False, ("learning", "command_select_notebook"), True),
        "sendChat": CommandSpec("chat", False, ("chat", "command_send_chat"), False),
        "setAudioOutput": CommandSpec(
            "media", False, ("playback_controller", "command_setAudioOutput"), False
        ),
        "setAudioTrack": CommandSpec("media", False, ("playback_controller", "command_setAudioTrack"), False),
        "setMuted": CommandSpec("media", False, ("playback_controller", "command_setMuted"), False),
        "setRate": CommandSpec("media", False, ("playback_controller", "command_setRate"), False),
        "setSelectedMedia": CommandSpec("media", False, ("chat", "command_set_selected_media"), True),
        "setSubtitleTrack": CommandSpec(
            "media", False, ("playback_controller", "command_setSubtitleTrack"), False
        ),
        "setVolume": CommandSpec("media", False, ("playback_controller", "command_setVolume"), False),
        "startQueue": CommandSpec("queue", False, ("queue_controller", "command_startQueue"), False),
        "stopChat": CommandSpec("chat", False, ("chat", "command_stop_chat"), True),
        "summarizeNotes": CommandSpec("learning", False, ("learning", "command_organize_notebook"), True),
        "syncNotebook": CommandSpec("learning", False, ("learning", "command_sync_notebook"), True),
        "textModelPolicy": CommandSpec("workspace", False, ("models", "command_text_model_policy"), True),
        "updateEntryNote": CommandSpec("learning", False, ("learning", "command_update_entry_note"), True),
    }
)


def command_domain(name):
    spec = COMMANDS.get(name)
    return spec.domain if spec else "notification"


def clears_status(name):
    spec = COMMANDS.get(name)
    return bool(spec and spec.clears_status)


def dispatch(host, name, payload):
    spec = COMMANDS.get(name)
    if spec is None:
        raise ValueError(f"未知操作：{name}")
    owner, method = spec.handler
    service = getattr(host, owner, None)
    if service is None:
        # Explicit unbound-call compatibility used by existing extension tests.
        if owner == "queue_controller":
            from .queue_controller import legacy_command

            return legacy_command(host, name, payload)
        from .feature_services import services_for

        service = getattr(services_for(host), owner)
    return getattr(service, method)(name, payload)


def try_feature(host, name, payload):
    spec = COMMANDS.get(name)
    if spec is None or not spec.feature:
        return False
    dispatch(host, name, payload)
    return True
