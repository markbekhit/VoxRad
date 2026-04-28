; RadSpeed Impressions — Windows desktop helper
; ----------------------------------------------------------------------------
; Select your dictated findings (in PowerScribe One, Word, or anywhere) and
; press Ctrl+I. The helper grabs the selection, sends it to RadSpeed, and
; pastes a guideline-aware impression back into the active field.
;
; Setup:
;   1. Install AutoHotkey v2 from https://www.autohotkey.com/
;   2. Double-click this file (RadSpeedImpressions.ahk) to run.
;   3. Look for the green 'H' AutoHotkey icon in the system tray (it's
;      AHK's default icon — hovering shows "RadSpeed Impressions").
;   4. (Optional) Right-click the tray icon → "Edit settings" to change the
;      hotkey or configure a "jump to IMPRESSION" key sequence.
;
; First-run behaviour: if no settings file exists, one is created next to
; this script with sensible defaults. Edit it and choose "Reload" from the
; tray menu to apply changes.
; ----------------------------------------------------------------------------

#Requires AutoHotkey v2.0
#SingleInstance Force
Persistent

; ----------------------------------------------------------------------------
; Settings
; ----------------------------------------------------------------------------
SettingsFile := A_ScriptDir "\RadSpeedImpressions.ini"

LoadSettings() {
    global Settings
    Settings := Map(
        "Hotkey",          "^i",
        "ApiUrl",          "https://dictation.markbekhit.com/api/impressions/text",
        "WithGuidelines",  "true",
        "PasteMode",       "goto_impression",
        "JumpKeys",        "",
        "Modality",        ""
    )
    if !FileExist(SettingsFile) {
        WriteDefaultSettings()
        return
    }
    for key in Settings.Clone() {
        val := IniRead(SettingsFile, "RadSpeed", key, "__missing__")
        if (val != "__missing__") {
            Settings[key] := val
        }
    }
}

WriteDefaultSettings() {
    ini := "
    (
; RadSpeed Impressions settings
; Edit this file then choose 'Reload' from the tray menu.
;
; Hotkey            AutoHotkey-style hotkey. ^=Ctrl, +=Shift, !=Alt, #=Win.
;                   Examples: ^i (Ctrl+I), ^!i (Ctrl+Alt+I), ^+i (Ctrl+Shift+I)
;
; PasteMode         How the impression is inserted:
;                     after_selection  = paste after the selected findings
;                     replace_selection= overwrite the selected findings
;                     at_cursor        = paste at current cursor (no select needed)
;                     goto_impression  = press JumpKeys first, then paste
;
; JumpKeys          Keys sent before pasting when PasteMode=goto_impression.
;                   Examples (depends on your PowerScribe template):
;                     {F2}            = next required field
;                     ^+i             = Ctrl+Shift+I
;                     {Tab 3}         = Tab three times
;                   Leave blank if you only use the other paste modes.
;
; WithGuidelines    true / false — apply Fleischner / BI-RADS / LI-RADS /
;                   PI-RADS / TI-RADS recommendations when relevant.
;
; Modality          Optional context hint sent with each request, e.g. CT chest.
;                   Leave blank for the helper to auto-detect from findings.

[RadSpeed]
Hotkey=^i
ApiUrl=https://dictation.markbekhit.com/api/impressions/text
WithGuidelines=true
PasteMode=goto_impression
JumpKeys=
Modality=
    )"
    FileAppend ini, SettingsFile
}

; ----------------------------------------------------------------------------
; Helpers used as callbacks — defined as named functions to avoid any AHK v2
; parser ambiguity around fat-arrow + comma in SetTimer / Menu.Add calls.
; ----------------------------------------------------------------------------
ClearTrayTipNow(*) {
    TrayTip()
}

OpenSettings(*) {
    Run('"' SettingsFile '"')
}

ReloadScript(*) {
    Reload()
}

OpenWebTool(*) {
    Run("https://dictation.markbekhit.com/impressions")
}

ExitScript(*) {
    ExitApp()
}

OnHotkey(*) {
    GenerateImpression()
}

; ----------------------------------------------------------------------------
; Tray menu
; ----------------------------------------------------------------------------
A_IconTip := "RadSpeed Impressions"
A_TrayMenu.Delete()
A_TrayMenu.Add("RadSpeed Impressions", NoOp)
A_TrayMenu.Disable("RadSpeed Impressions")
A_TrayMenu.Add()
A_TrayMenu.Add("Edit settings",     OpenSettings)
A_TrayMenu.Add("Reload",            ReloadScript)
A_TrayMenu.Add("Open RadSpeed web", OpenWebTool)
A_TrayMenu.Add()
A_TrayMenu.Add("Exit",              ExitScript)

NoOp(*) {
    return
}

; ----------------------------------------------------------------------------
; Boot
; ----------------------------------------------------------------------------
LoadSettings()
HotKey Settings["Hotkey"], OnHotkey

TrayTip("v2 — Press " HumanHotkey(Settings["Hotkey"]) " to generate an impression.", "RadSpeed", 0x10)
SetTimer ClearTrayTipNow, -3000

; ----------------------------------------------------------------------------
; Main flow
; ----------------------------------------------------------------------------
GenerateImpression() {
    global Settings

    pasteMode := Settings["PasteMode"]
    needsSelection := pasteMode != "at_cursor"

    ; Save and clear the clipboard so we can detect a fresh copy.
    savedClip := A_Clipboard
    A_Clipboard := ""

    findings := ""
    if (needsSelection) {
        Send "^c"
        if !ClipWait(0.6) {
            A_Clipboard := savedClip
            TrayTip("Select the findings text first, then press the hotkey.", "RadSpeed", 0x2)
            SetTimer ClearTrayTipNow, -3000
            return
        }
        findings := A_Clipboard
        A_Clipboard := savedClip
    } else {
        ; at_cursor: read the entire active document. Best-effort: select all,
        ; copy, then restore. Some apps will not allow this — fall back gracefully.
        Send "^a"
        Sleep 50
        Send "^c"
        if !ClipWait(0.6) {
            A_Clipboard := savedClip
            TrayTip("Could not read findings. Try selecting them first.", "RadSpeed", 0x2)
            SetTimer ClearTrayTipNow, -3000
            return
        }
        findings := A_Clipboard
        A_Clipboard := savedClip
        ; Move cursor to end of document so the impression appends.
        Send "{End}"
    }

    findings := Trim(findings)
    if (StrLen(findings) < 5) {
        TrayTip("Findings too short. Select more text and try again.", "RadSpeed", 0x2)
        SetTimer ClearTrayTipNow, -3000
        return
    }
    if (StrLen(findings) > 8000) {
        TrayTip("Findings too long (>8000 chars). Trim and retry.", "RadSpeed", 0x2)
        SetTimer ClearTrayTipNow, -3000
        return
    }

    TrayTip("Generating impression...", "RadSpeed", 0x10)

    impression := ""
    try {
        impression := PostJsonForText(
            Settings["ApiUrl"],
            BuildRequestBody(findings, Settings["Modality"], Settings["WithGuidelines"])
        )
    } catch as e {
        TrayTip()
        TrayTip("Error: " e.Message, "RadSpeed", 0x3)
        SetTimer ClearTrayTipNow, -4000
        return
    }

    impression := Trim(impression, " `r`n`t")
    if (impression = "") {
        TrayTip()
        TrayTip("Empty response from server.", "RadSpeed", 0x3)
        SetTimer ClearTrayTipNow, -3000
        return
    }

    PasteImpression(impression, pasteMode)

    TrayTip()
    TrayTip("Done.", "RadSpeed", 0x10)
    SetTimer ClearTrayTipNow, -1500
}

PasteImpression(impression, mode) {
    global Settings

    ; Determine cursor placement before paste.
    if (mode = "after_selection") {
        ; Move to the end of the current selection without losing it, then
        ; insert a blank line and an "IMPRESSION:" heading.
        Send "{Right}"
        Send "{End}"
        Send "{Enter 2}IMPRESSION:{Enter}"
    } else if (mode = "replace_selection") {
        ; Selection still active from the earlier ^c — pasting will overwrite.
    } else if (mode = "goto_impression") {
        if (Settings["JumpKeys"] != "") {
            Send Settings["JumpKeys"]
            Sleep 120
        } else {
            ; Fallback: behave like after_selection if no JumpKeys configured.
            Send "{Right}{End}{Enter 2}IMPRESSION:{Enter}"
        }
    } else if (mode = "at_cursor") {
        ; Cursor already at end of doc from the read step — leave it.
        Send "{Enter 2}IMPRESSION:{Enter}"
    }

    savedClip := A_Clipboard
    A_Clipboard := impression
    if !ClipWait(0.5) {
        A_Clipboard := savedClip
        throw Error("Clipboard write failed")
    }
    Send "^v"
    Sleep 120
    A_Clipboard := savedClip
}

; ----------------------------------------------------------------------------
; HTTP + JSON helpers
; ----------------------------------------------------------------------------
BuildRequestBody(findings, modality, withGuidelines) {
    body := '{"findings":' . JsonStr(findings)
    if (modality != "") {
        body .= ',"modality":' . JsonStr(modality)
    }
    body .= ',"with_guidelines":' . (StrLower(withGuidelines) = "true" ? "true" : "false")
    body .= "}"
    return body
}

JsonStr(s) {
    s := StrReplace(s, "\", "\\")
    s := StrReplace(s, '"', '\"')
    s := StrReplace(s, "`r`n", "\n")
    s := StrReplace(s, "`r", "\n")
    s := StrReplace(s, "`n", "\n")
    s := StrReplace(s, "`t", "\t")
    return '"' s '"'
}

PostJsonForText(url, body) {
    req := ComObject("WinHttp.WinHttpRequest.5.1")
    req.Open("POST", url, false)
    req.SetRequestHeader("Content-Type", "application/json")
    req.SetRequestHeader("Accept", "text/plain")
    req.SetTimeouts(5000, 10000, 30000, 30000)
    try {
        req.Send(body)
    } catch as e {
        throw Error("Network error: " e.Message)
    }
    if (req.Status >= 400) {
        ; The server returns plain-text error detail in the body for our endpoint
        ; or JSON detail for FastAPI-default errors — show whichever we get.
        msg := Trim(req.ResponseText)
        ; Strip a FastAPI-style {"detail":"..."} wrapper if present.
        if (SubStr(msg, 1, 11) = '{"detail":"' && SubStr(msg, -1) = '}') {
            msg := SubStr(msg, 12, StrLen(msg) - 13)
            msg := StrReplace(msg, '\"', '"')
            msg := StrReplace(msg, "\\", "\")
        }
        throw Error("HTTP " req.Status ": " msg)
    }
    return req.ResponseText
}

HumanHotkey(hk) {
    out := ""
    if (InStr(hk, "^"))
        out .= "Ctrl+"
    if (InStr(hk, "+"))
        out .= "Shift+"
    if (InStr(hk, "!"))
        out .= "Alt+"
    if (InStr(hk, "#"))
        out .= "Win+"
    last := SubStr(hk, 0)
    out .= StrUpper(last)
    return out
}
