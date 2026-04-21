class ClaudeStandup < Formula
  desc "Daily standup reports from Claude Code activity logs"
  homepage "https://github.com/theburrowhub/claude-standup"
  url "https://github.com/theburrowhub/claude-standup/releases/download/vVERSION/claude-standup-VERSION-macos-arm64.tar.gz"
  sha256 "SHA256_PLACEHOLDER"
  license "MIT"

  def install
    bin.install "claude-standup"
  end

  service do
    run [opt_bin/"claude-standup", "daemon", "run"]
    keep_alive true
    log_path var/"log/claude-standup.log"
    error_log_path var/"log/claude-standup.log"
  end

  test do
    assert_match "usage:", shell_output("#{bin}/claude-standup --help", 0)
  end
end
