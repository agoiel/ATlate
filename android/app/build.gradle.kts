import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.manhwatranslator"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.manhwatranslator"
        minSdk = 26
        targetSdk = 35
        versionCode = 7
        versionName = "0.6.3"
    }

    // Постоянный ключ подписи: новые версии ставятся поверх старых без удаления.
    signingConfigs {
        create("shared") {
            storeFile = file("signing.keystore")
            storePassword = "manhwa123"
            keyAlias = "manhwa"
            keyPassword = "manhwa123"
        }
    }

    buildTypes {
        getByName("debug") {
            signingConfig = signingConfigs.getByName("shared")
        }
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("shared")
        }
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    // Распознавание текста ML Kit через сервисы Google Play (модели не входят в APK)
    // докачка модулей распознавания (ModuleInstallClient)
    implementation("com.google.android.gms:play-services-base:18.5.0")
    implementation("com.google.android.gms:play-services-mlkit-text-recognition:19.0.1")
    implementation("com.google.android.gms:play-services-mlkit-text-recognition-korean:16.0.1")
    implementation("com.google.android.gms:play-services-mlkit-text-recognition-japanese:16.0.1")
    implementation("com.google.android.gms:play-services-mlkit-text-recognition-chinese:16.0.1")
}
