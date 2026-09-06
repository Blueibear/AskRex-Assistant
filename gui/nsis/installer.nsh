!macro customUnInstall
  DetailPrint "Stopping AskRex background runtime..."
  nsExec::ExecToLog '"$INSTDIR\resources\python\python.exe" -I -m rex.background.cli stop --runtime-root "$APPDATA\rex-gui" --wait-seconds 15'
  Pop $0

  DetailPrint "Ending AskRex background startup task if it is active..."
  nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /End /TN "AskRex Background Runtime"'
  Pop $0

  DetailPrint "Removing AskRex background startup task..."
  nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /Delete /TN "AskRex Background Runtime" /F'
  Pop $0
  ${If} $0 != 0
    DetailPrint "Task Scheduler deletion did not succeed; verifying whether the task is already absent..."
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$$ErrorActionPreference = ''Stop''; try { $$service = New-Object -ComObject ''Schedule.Service''; $$service.Connect(); $$folder = $$service.GetFolder(''\\''); $$null = $$folder.GetTask(''AskRex Background Runtime''); exit 1 } catch { if (($$_.Exception.HResult -band 0xFFFF) -eq 2) { exit 0 }; exit 1 }"'
    Pop $0
    ${If} $0 != 0
      Abort "AskRex could not confirm removal of the background startup task."
    ${EndIf}
  ${EndIf}
!macroend
