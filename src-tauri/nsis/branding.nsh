; Reuse the existing installation when the product's displayed name changes.
; Application data remains under the unchanged local.clash-replay-tree ID.
!define LEGACY_PRODUCT_NAME "皇室战争原生战斗复盘器"
!define LEGACY_PRODUCT_KEY "Software\clash-replay-tree\${LEGACY_PRODUCT_NAME}"
!define LEGACY_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${LEGACY_PRODUCT_NAME}"

!macro NSIS_HOOK_PREINSTALL
  ReadRegStr $R8 HKCU "${MANUPRODUCTKEY}" ""
  ${If} $R8 == ""
    ReadRegStr $R8 HKCU "${LEGACY_PRODUCT_KEY}" ""
    ${If} $R8 != ""
    ${AndIf} ${FileExists} "$R8\${MAINBINARYNAME}.exe"
      StrCpy $INSTDIR $R8
      SetOutPath $INSTDIR
    ${EndIf}
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ReadRegStr $R8 HKCU "${LEGACY_PRODUCT_KEY}" ""
  ${If} $R8 != ""
  ${AndIf} $R8 == $INSTDIR
    ; The new registration has been written and files replaced in the same directory.
    DeleteRegKey HKCU "${LEGACY_UNINSTALL_KEY}"
    DeleteRegKey HKCU "${LEGACY_PRODUCT_KEY}"
    ${If} ${FileExists} "$DESKTOP\${LEGACY_PRODUCT_NAME}.lnk"
      ${If} ${FileExists} "$DESKTOP\${PRODUCTNAME}.lnk"
        Delete "$DESKTOP\${LEGACY_PRODUCT_NAME}.lnk"
      ${Else}
        Rename "$DESKTOP\${LEGACY_PRODUCT_NAME}.lnk" "$DESKTOP\${PRODUCTNAME}.lnk"
      ${EndIf}
    ${EndIf}
    ${If} ${FileExists} "$SMPROGRAMS\${LEGACY_PRODUCT_NAME}.lnk"
      ${If} ${FileExists} "$SMPROGRAMS\${PRODUCTNAME}.lnk"
        Delete "$SMPROGRAMS\${LEGACY_PRODUCT_NAME}.lnk"
      ${Else}
        Rename "$SMPROGRAMS\${LEGACY_PRODUCT_NAME}.lnk" "$SMPROGRAMS\${PRODUCTNAME}.lnk"
      ${EndIf}
    ${EndIf}
  ${EndIf}
!macroend
