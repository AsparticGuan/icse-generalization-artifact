define i8 @fold_add_umax_to_usub(i8 %a) {
  %sel = call i8 @llvm.usub.sat.i8(i8 %a, i8 10)
  ret i8 %sel
}

; Function Attrs: nocallback nosync nounwind speculatable willreturn memory(none)
declare i8 @llvm.umax.i8(i8, i8) #0

; Function Attrs: nocallback nosync nounwind speculatable willreturn memory(none)
declare i8 @llvm.usub.sat.i8(i8, i8) #0

attributes #0 = { nocallback nosync nounwind speculatable willreturn memory(none) }