define i1 @test(float %0) {
  entry:
  %oeq = fcmp oeq float %0, 0.000000e+00
  ret i1 %oeq
}
