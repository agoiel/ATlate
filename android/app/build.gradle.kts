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
        versionCode = 10
        versionName = "0.6.7"
        // только процессоры телефонов: офлайн-переводчик (ML Kit) везёт свой код под каждый процессор,
        // а x86 нужен лишь эмуляторам и почти не встречается в телефонах
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    // BuildConfig.VERSION_NAME — номер версии в окне настроек берётся из сборки
    buildFeatures {
        buildConfig = true
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

    // нативный код офлайн-переводчика хранится в APK сжатым: файл для скачивания почти вдвое меньше
    // (при установке Android распакует его сам)
    packaging {
        jniLibs {
            useLegacyPackaging = true
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
    // перевод без интернета на телефоне (модели языков скачиваются по кнопке, ~30 МБ каждая)
    implementation("com.google.mlkit:translate:17.0.3")
    // какой язык у текста на латинице — для офлайн-перевода «Латиницы» (модель в сервисах Google Play)
    implementation("com.google.android.gms:play-services-mlkit-language-id:17.0.0")
}
