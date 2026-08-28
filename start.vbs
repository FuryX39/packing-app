Option Explicit
' Тихий запуск GUI без окна cmd. Ярлык лучше вести сюда, не на start.bat.
Dim fso, sh, root, pythonw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  sh.Run "cmd /c """ & root & "\start.bat"" setup", 1, True
End If
If fso.FileExists(pythonw) Then
  sh.Run """" & pythonw & """ """ & root & "\launch.pyw""", 0, False
Else
  MsgBox "Не удалось запустить программу упаковщиков." & vbCrLf & _
         "Нет .venv\Scripts\pythonw.exe", vbCritical, "Warehouse Packing App"
End If
