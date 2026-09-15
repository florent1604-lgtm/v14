$ErrorActionPreference = "Stop"

$checks = @(
    @{ Name = "Exporteur V14"; Url = "http://127.0.0.1:9108/metrics" },
    @{ Name = "Prometheus"; Url = "http://127.0.0.1:9090/-/ready" },
    @{ Name = "Grafana"; Url = "http://127.0.0.1:3000/api/health" }
)

foreach ($check in $checks) {
    try {
        $response = Invoke-WebRequest -Uri $check.Url -UseBasicParsing -TimeoutSec 5
        Write-Output ("{0}: OK ({1})" -f $check.Name, $response.StatusCode)
    }
    catch {
        Write-Output ("{0}: INDISPONIBLE ({1})" -f $check.Name, $_.Exception.Message)
    }
}
