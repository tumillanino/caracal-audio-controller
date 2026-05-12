%global debug_package %{nil}
%global upstream_version %{?version_override}%{!?version_override:0.3.0}
%global github_owner %{?github_owner_override}%{!?github_owner_override:caracal-os}
%global github_repo %{?github_repo_override}%{!?github_repo_override:caracal-audio-controller}
%global source_tag %{?source_tag_override}%{!?source_tag_override:v%{upstream_version}}
%global source_dir_name %{github_repo}-%{upstream_version}

Name:           caracal-audio-controller
Version:        %{upstream_version}
Release:        %{?release_override}%{!?release_override:1}%{?dist}
Summary:        KDE tray controller for Caracal OS audio maintenance actions
License:        MIT
URL:            https://github.com/%{github_owner}/%{github_repo}
Source0:        %{url}/archive/refs/tags/%{source_tag}.tar.gz#/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3
Requires:       python3
Requires:       python3-pyside6

%description
caracal-audio-controller provides a KDE system tray icon for running Caracal OS
audio maintenance recipes such as yabridge sync, PipeWire restart, virtual
channel setup, and system plugin routing.

%prep
%autosetup -n %{source_dir_name}

%build
python3 -c "import ast, pathlib; ast.parse(pathlib.Path('src/caracal_audio_controller/app.py').read_text())"

%install
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/%{name}/src/caracal_audio_controller
install -d %{buildroot}%{_datadir}/%{name}/assets
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/icons/hicolor/16x16/apps
install -d %{buildroot}%{_sysconfdir}/xdg/autostart

cp -a src/caracal_audio_controller %{buildroot}%{_datadir}/%{name}/src/
cp -a assets/*.svg assets/*.svg %{buildroot}%{_datadir}/%{name}/assets/
install -pm0644 assets/icon-white.svg %{buildroot}%{_datadir}/icons/hicolor/16x16/apps/caracal-audio-controller.svg

cat > %{buildroot}%{_bindir}/caracal-audio-controller <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec python3 /usr/share/caracal-audio-controller/src/caracal_audio_controller/app.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/caracal-audio-controller

install -Dpm0644 packaging/caracal-audio-controller.desktop %{buildroot}%{_datadir}/applications/caracal-audio-controller.desktop
install -Dpm0644 packaging/caracal-audio-controller-autostart.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/caracal-audio-controller.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/caracal-audio-controller
%{_datadir}/%{name}/assets/*
%{_datadir}/%{name}/src/caracal_audio_controller/*
%{_datadir}/icons/hicolor/16x16/apps/caracal-audio-controller.svg
%{_datadir}/applications/caracal-audio-controller.desktop
%{_sysconfdir}/xdg/autostart/caracal-audio-controller.desktop
