Option Explicit
Dim shell, fs, root, candidate, command, profile, directory, entry, names, name, extra, wait, result, stream
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
shell.Environment("PROCESS")("PYTHONUTF8") = "1"
shell.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
command = ""
If fs.FileExists(root & "\python_path.txt") Then
  Set stream = CreateObject("ADODB.Stream")
  stream.Type = 2
  stream.Charset = "utf-8"
  stream.Open
  stream.LoadFromFile root & "\python_path.txt"
  candidate = Trim(Replace(Replace(stream.ReadText, vbCr, ""), vbLf, ""))
  stream.Close
  If Left(candidate, 1) = ChrW(&HFEFF) Then candidate = Mid(candidate, 2)
  candidate = Replace(candidate, Chr(34), "")
  If Not fs.FileExists(candidate) Then candidate = root & "\" & candidate
  If fs.FileExists(candidate) Then command = Chr(34) & candidate & Chr(34)
End If
If command = "" Then
  profile = shell.ExpandEnvironmentStrings("%USERPROFILE%")
  For Each candidate In Array(root & "\runtime\pythonw.exe", profile & "\anaconda3\pythonw.exe", profile & "\miniconda3\pythonw.exe")
    If fs.FileExists(candidate) Then
      command = Chr(34) & candidate & Chr(34)
      Exit For
    End If
  Next
End If
If command = "" Then
  directory = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python"
  If fs.FolderExists(directory) Then
    For Each entry In fs.GetFolder(directory).SubFolders
      candidate = entry.Path & "\pythonw.exe"
      If fs.FileExists(candidate) Then command = Chr(34) & candidate & Chr(34)
    Next
  End If
End If
If command = "" Then
  names = Array("pyw.exe", "py.exe", "pythonw.exe", "python.exe")
  For Each directory In Split(shell.Environment("PROCESS")("PATH"), ";")
    directory = Replace(directory, Chr(34), "")
    If directory <> "" And InStr(LCase(directory), "\windowsapps") = 0 Then
      For Each name In names
        candidate = fs.BuildPath(directory, name)
        If fs.FileExists(candidate) Then
          command = Chr(34) & candidate & Chr(34)
          If name = "pyw.exe" Or name = "py.exe" Then command = command & " -3"
          Exit For
        End If
      Next
    End If
    If command <> "" Then Exit For
  Next
End If
If command = "" Then
  MsgBox "Python 3.9+ was not found. Install Python, or write its full executable path in python_path.txt next to start.vbs.", vbExclamation, "ITC-C80"
  WScript.Quit 1
End If
extra = ""
wait = True
If WScript.Arguments.Count > 0 Then
  If WScript.Arguments(0) = "--self-test" Then
    extra = " --self-test"
    wait = True
  End If
End If
result = shell.Run(command & " " & Chr(34) & root & "\src\script_launcher.py" & Chr(34) & extra, 0, wait)
If result <> 0 Then MsgBox "Launcher failed (exit " & result & "). Please send the error text and data\logs\script_launcher.log / launcher.log.", vbExclamation, "ITC-C80"
WScript.Quit result
