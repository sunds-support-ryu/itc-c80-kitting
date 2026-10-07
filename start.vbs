Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
exe = root & "\release\native\ITC-C80-Launcher-1.0.exe"
If fs.FileExists(exe) Then
  shell.Run Chr(34) & exe & Chr(34), 0, False
Else
  shell.CurrentDirectory = root
  shell.Run "py -3 " & Chr(34) & root & "\src\bootstrap.py" & Chr(34), 0, False
End If
