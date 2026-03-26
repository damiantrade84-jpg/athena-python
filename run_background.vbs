' run_background.vbs
' Launches Sentinel Pro as a true background process — no visible CMD window.
' The process is NOT tied to any console or user session.
' Screen sleep, window minimize, tab switching — none of these will stop it.
'
' Usage: Double-click this file (or run: wscript run_background.vbs)
' Stop:  Use Task Manager -> kill "python.exe" (or use stop_sentinel.bat)

Dim fso, scriptDir
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

Dim shell
Set shell = CreateObject("WScript.Shell")

' Activate venv if present
Dim pythonCmd
Dim venvPy
venvPy = scriptDir & "\.venv\Scripts\python.exe"

If fso.FileExists(venvPy) Then
    pythonCmd = """" & venvPy & """"
Else
    pythonCmd = "py -3.13"
End If

Dim cmd
cmd = pythonCmd & " """ & scriptDir & "\athena.py"""

' 0 = hidden window, False = don't wait for exit
shell.Run cmd, 0, False

MsgBox "Sentinel Pro started in background." & vbCrLf & _
       "It will keep running even if this window is minimized or screen sleeps." & vbCrLf & vbCrLf & _
       "To stop: run stop_sentinel.bat", vbInformation, "Sentinel Pro"
