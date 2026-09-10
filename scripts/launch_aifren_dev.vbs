' Opens the developer GUI without exposing a PowerShell, cmd.exe, or Python
' console. The GUI delegates lifecycle work to the existing batch scripts.
Option Explicit

Dim shell, fileSystem, repositoryRoot, launcher, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
repositoryRoot = fileSystem.GetParentFolderName(fileSystem.GetParentFolderName(WScript.ScriptFullName))
launcher = fileSystem.BuildPath(repositoryRoot, "scripts\show_aifren_dev_launcher.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & launcher & Chr(34)
shell.Run command, 0, False
