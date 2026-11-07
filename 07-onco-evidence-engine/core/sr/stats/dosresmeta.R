#!/usr/bin/env Rscript
# Two-stage dose-response meta-analysis (dosresmeta) with restricted cubic splines.
# Usage: Rscript dosresmeta.R <input_csv> <curve_csv> <plot_png> <outcome>
# input_csv columns: study, dose, cases, n  (cumulative-incidence aggregated data)

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]; png_path <- args[3]
label <- if (length(args) >= 4) args[4] else "outcome"

suppressMessages({ library(dosresmeta); library(rms) })
d <- read.csv(incsv, stringsAsFactors = FALSE)

# Per-study log RR + SE vs that study's lowest-dose reference (GL covariance uses cases/n).
d <- do.call(rbind, lapply(split(d, d$study), function(s) {
  s <- s[order(s$dose), ]
  ca <- pmax(s$cases, 0.5); nn <- s$n           # continuity correction for empty cells
  pr <- ca / nn
  s$logrr <- log(pr / pr[1])
  s$se <- sqrt(1 / ca - 1 / nn + 1 / ca[1] - 1 / nn[1])
  s$se[1] <- NA                                  # reference row
  s
}))

knots <- quantile(d$dose, c(.1, .5, .9))
ok <- tryCatch({
  mod <- dosresmeta(formula = logrr ~ rcs(dose, knots), id = study, type = "ci",
                    se = se, cases = cases, n = n, data = d, method = "reml")
  grid <- seq(min(d$dose), max(d$dose), length.out = 50)
  pred <- predict(mod, newdata = data.frame(dose = grid), expo = TRUE)
  out <- data.frame(dose = round(grid, 4), est = round(pred$pred, 4),
                    ci_lb = round(pred$ci.lb, 4), ci_ub = round(pred$ci.ub, 4))
  # Non-linearity: Wald test on the spline (2nd) term.
  pnl <- tryCatch(round(waldtest(b = coef(mod), Sigma = vcov(mod), Terms = 2)$result$chi2["P"], 4),
                  error = function(e) NA)
  out <- rbind(out, data.frame(dose = "PNL", est = ifelse(is.na(pnl), -1, pnl),
                               ci_lb = NA, ci_ub = NA))
  write.csv(out, outcsv, row.names = FALSE)
  tryCatch({
    png(png_path, width = 900, height = 650, res = 120)
    plot(grid, pred$pred, type = "l", lwd = 2, xlab = paste("Dose"),
         ylab = paste("Relative risk —", label), ylim = range(pred$ci.lb, pred$ci.ub))
    lines(grid, pred$ci.lb, lty = 2); lines(grid, pred$ci.ub, lty = 2)
    abline(h = 1, col = "grey60"); dev.off()
  }, error = function(e) NULL)
  TRUE
}, error = function(e) { cat("ERROR:", conditionMessage(e), "\n"); FALSE })

if (!ok) {
  write.csv(data.frame(dose = character(), est = numeric(), ci_lb = numeric(), ci_ub = numeric()),
            outcsv, row.names = FALSE)
  quit(status = 1)
}
